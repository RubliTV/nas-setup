"""Bounded Compose subset, parsed without host environment or file expansion.

Supported: image, environment, published TCP/UDP ports, existing NAS bind paths,
project-local named volumes/bridge networks, restart, depends_on, command,
entrypoint, user, working_dir, hostname, read_only, mem_limit, cpus, healthcheck.
Unknown options fail closed. No builds, external resources, privileges or files.
"""
import re
import json
from pathlib import Path
import yaml
from .models import SetupError
from .system import bind

SLUG=re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
IMAGE=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._/:@-]{0,255}\Z')
VARIABLE=re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-|-|:\?|\?)([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)')
SECRET=re.compile(r'pass|secret|token|key|credential|auth',re.I)
SERVICE_KEYS={'image','environment','ports','volumes','restart','depends_on','command','entrypoint','user','working_dir','hostname','read_only','mem_limit','cpus','healthcheck','networks'}


class StrictLoader(yaml.SafeLoader):
    def compose_node(self,parent,index):
        if self.check_event(yaml.AliasEvent):raise SetupError('compose_alias_unsupported')
        return super().compose_node(parent,index)
    def construct_mapping(self,node,deep=False):
        result={}
        for key,value in node.value:
            k=self.construct_object(key,deep=deep)
            if not isinstance(k,str) or k in result:raise SetupError('compose_duplicate_key')
            result[k]=self.construct_object(value,deep=deep)
        return result


def require(condition,code='compose_invalid'):
    if not condition:raise SetupError(code)


def keys(value,allowed):
    require(isinstance(value,dict))
    require(set(value)<=set(allowed),'compose_option_unsupported')


def host_path(value,roots):
    require(isinstance(value,str),'unsafe_storage')
    path=Path(value)
    require(path.is_absolute() and path.is_dir() and path.resolve()==path,'unsafe_storage')
    require(any(path!=root and path.is_relative_to(root) for root in roots),'unsafe_storage')
    # App-private namespaces and credentials must not be exposed to imports.
    root=next(root for root in roots if path.is_relative_to(root))
    require(not any(p.startswith('.') for p in path.relative_to(root).parts),'unsafe_storage')
    return path


def port(value):
    require(isinstance(value,str),'compose_port_invalid')
    match=re.fullmatch(r'(?:(127\.0\.0\.1|0\.0\.0\.0):)?([0-9]{1,5}):([0-9]{1,5})(?:/(tcp|udp))?',value)
    require(match is not None,'compose_port_invalid')
    host,container=int(match[2]),int(match[3]);protocol=match[4] or 'tcp'
    require(1024<=host<=65535 and host!=8443 and 1<=container<=65535,'compose_port_invalid')
    return {'host':host,'container':container,'protocol':protocol}


def preview(config):
    return {'services':[{'name':name,'image':service['image'],
        'ports':[port(p) for p in service.get('ports',[])],
        'mounts':[{'source':m['source'],'target':m['target'],'read_only':m.get('read_only',False)} for m in service.get('volumes',[])]}
        for name,service in config['services'].items()]}


def parse_compose(text,variables,roots):
    require(isinstance(text,str) and 0<len(text.encode())<=65536,'compose_too_large')
    require(isinstance(variables,dict) and len(variables)<=64)
    require(all(isinstance(k,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',k) and isinstance(v,str) and len(v)<=4096 and '\x00' not in v for k,v in variables.items()))
    try:config=yaml.load(text,Loader=StrictLoader)
    except (yaml.YAMLError,RecursionError,ValueError):raise SetupError('compose_invalid') from None
    keys(config,{'services','volumes','networks','name','version'})
    needs={};nodes=0
    def expand(value,depth=0,public=False):
        nonlocal nodes
        nodes+=1;require(nodes<=4096 and depth<=20,'compose_too_large')
        if isinstance(value,dict):
            require(all('$' not in k for k in value),'compose_variable_invalid')
            return {k:expand(v,depth+1,public or k in ('image','ports','volumes','name')) for k,v in value.items()}
        if isinstance(value,list):return [expand(v,depth+1,public) for v in value]
        if not isinstance(value,str):return value
        require('\x00' not in value)
        escaped=value.replace('$$','\x00')
        def replace(match):
            name=match[1] or match[4];op=match[2];default=match[3] or '';supplied=variables.get(name)
            require(not(public and SECRET.search(name)),'compose_secret_field')
            if supplied is not None and (supplied or op not in (':-',':?')):return supplied
            if op in ('-',':-'):return default
            needs[name]={'name':name,'secret':bool(SECRET.search(name))};return ''
        expanded=VARIABLE.sub(replace,escaped)
        require(len(expanded)<=8192,'compose_too_large')
        # Nested interpolation and malformed syntax are deliberately unsupported.
        require('${' not in expanded,'compose_variable_invalid')
        return expanded.replace('\x00','$')
    config=expand(config)
    if needs:return {'needs':list(needs.values()),'config':None,'summary':None}
    try:require(len(json.dumps(config).encode())<=131072,'compose_too_large')
    except TypeError:raise SetupError('compose_invalid') from None
    services=config.get('services');require(isinstance(services,dict) and 1<=len(services)<=16)
    volumes=config.get('volumes') or {};networks=config.get('networks') or {}
    require(isinstance(volumes,dict) and len(volumes)<=32)
    for name,v in volumes.items():
        require(bool(SLUG.fullmatch(name)));require(v in ({},None),'compose_volume_unsupported')
    require(isinstance(networks,dict) and len(networks)<=16)
    for name,n in networks.items():
        require(bool(SLUG.fullmatch(name)));n=n or {};keys(n,{'driver','internal'})
        require(n.get('driver','bridge')=='bridge' and isinstance(n.get('internal',False),bool),'compose_network_unsupported')
    roots=[Path(r) for r in roots];published=set()
    for name,s in services.items():
        require(bool(SLUG.fullmatch(name)));keys(s,SERVICE_KEYS)
        require(isinstance(s.get('image'),str) and bool(IMAGE.fullmatch(s['image'])),'compose_image_invalid')
        require(':' in s['image'] or '@sha256:' in s['image'],'compose_image_tag_required')
        env=s.get('environment',{})
        if isinstance(env,list):
            mapped={}
            for item in env:
                require(isinstance(item,str) and '=' in item,'compose_environment_invalid')
                k,v=item.split('=',1);require(k not in mapped);mapped[k]=v
            env=mapped
        require(isinstance(env,dict) and len(env)<=128,'compose_environment_invalid')
        for k,v in env.items():require(bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',k)) and isinstance(v,(str,int,float,bool)),'compose_environment_invalid')
        if env:s['environment']={k:str(v).lower() if isinstance(v,bool) else str(v) for k,v in env.items()}
        ports=s.get('ports',[]);require(isinstance(ports,list) and len(ports)<=32)
        for p in ports:
            parsed=port(p);key=(parsed['host'],parsed['protocol']);require(key not in published,'port_conflict');published.add(key)
        mounts=s.get('volumes',[]);require(isinstance(mounts,list) and len(mounts)<=32)
        converted=[];targets=set()
        for mount in mounts:
            if isinstance(mount,str):
                parts=mount.split(':');require(len(parts) in (2,3),'compose_mount_invalid')
                source,target=parts[:2];mode=parts[2] if len(parts)==3 else 'rw';require(mode in ('ro','rw'),'compose_mount_invalid')
                m={'type':'bind' if source.startswith('/') else 'volume','source':source,'target':target,'read_only':mode=='ro'}
            else:
                keys(mount,{'type','source','target','read_only','bind'})
                m=dict(mount);require(m.get('type') in ('bind','volume'),'compose_mount_invalid')
                if 'bind' in m:require(m['bind']=={'create_host_path':False},'compose_mount_invalid')
            source,target=m.get('source'),m.get('target')
            require(isinstance(target,str) and target.startswith('/') and target!='/' and '..' not in Path(target).parts and '\x00' not in target,'compose_mount_invalid')
            require(target not in targets and isinstance(m.get('read_only',False),bool),'compose_mount_invalid');targets.add(target)
            if m['type']=='bind':m=bind(host_path(source,roots),target,m.get('read_only',False))
            else:require(isinstance(source,str) and source in volumes,'compose_volume_unsupported')
            converted.append(m)
        if mounts:s['volumes']=converted
        require(s.get('restart','no') in ('no','always','unless-stopped','on-failure'),'compose_invalid')
        s.setdefault('restart','no')
        deps=s.get('depends_on',[])
        require(isinstance(deps,(list,dict)))
        for dep in deps:
            require(isinstance(dep,str) and dep in services and dep!=name)
            if isinstance(deps,dict):
                keys(deps[dep],{'condition'});require(deps[dep].get('condition') in ('service_started','service_healthy','service_completed_successfully'))
        nets=s.get('networks',[]);require(isinstance(nets,list) and all(isinstance(n,str) and n in networks for n in nets),'compose_network_unsupported')
        for key in ('command','entrypoint'):
            if key in s:require(isinstance(s[key],str) or isinstance(s[key],list) and all(isinstance(v,str) for v in s[key]))
        for key in ('user','working_dir','hostname','mem_limit'):
            if key in s:require(isinstance(s[key],str) and len(s[key])<=256)
        if 'read_only' in s:require(isinstance(s['read_only'],bool))
        if 'cpus' in s:require(isinstance(s['cpus'],(int,float)) and 0<float(s['cpus'])<=256)
        if 'healthcheck' in s:
            h=s['healthcheck'];keys(h,{'test','interval','timeout','start_period','retries','disable'})
            if 'test' in h:require(isinstance(h['test'],list) and all(isinstance(v,str) for v in h['test']))
            for key in ('interval','timeout','start_period'):
                if key in h:require(isinstance(h[key],str) and bool(re.fullmatch(r'[0-9]+(?:ms|s|m|h)',h[key])))
            if 'retries' in h:require(type(h['retries']) is int and 1<=h['retries']<=100)
            if 'disable' in h:require(isinstance(h['disable'],bool))
    config.pop('name',None);config.pop('version',None)
    return {'needs':[],'config':config,'summary':preview(config)}


def compose_text(config):
    """Freeze interpolation: Compose must never expand host environment at deploy."""
    def literal(v):
        if isinstance(v,str):return v.replace('$','$$')
        if isinstance(v,list):return [literal(i) for i in v]
        if isinstance(v,dict):return {k:literal(i) for k,i in v.items()}
        return v
    return yaml.safe_dump(literal(config),sort_keys=False)
