"""Permanent manager worker API (all calls require an authenticated session).

call(op, {'session': id, 'data': body}): catalog -> {apps}; options ->
{volumes,users:[{name,uid,gid}],shares:[{name,path,volume}]}; overview ->
{docker,demo,containers:[{id,name,image,state,status,project,managed,actions,
ports:[{host,container,protocol}],cpu,memory}]}. plan accepts kind=catalog,
app,volume,media,user,port OR kind=compose,name,compose,variables. Returns
{valid,needs:[{name,secret}],plan_id?,summary:{name,services:[{name,image,
ports,mounts}]}}. deploy accepts only plan_id. action accepts id and action
(start/stop/restart/update); logs accepts id -> {text}; job accepts optional id
-> persisted job or null. overview accepts optional offset and returns total and
next_offset for bounded 64-container pages. retry accepts a failed/interrupted
deployment job id. prepare_docker accepts an empty body. Jobs expose id/status/operation/target/steps/error.

manager.sqlite contains jobs(id TEXT,body TEXT,created REAL), with the same
running status shape as wizard jobs, plus private project metadata. Config/env
values stay in private project files, never in API job records or diagnostics.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import sqlite3
import threading
import tempfile
import time
import uuid

from .catalog import APPS, render_catalog
from .compose_import import SLUG, compose_text, host_path, parse_compose, port, preview, require
from .models import SetupError, Selection
from .storage import volume_path, safe_directory

DEMO_EXTERNAL=hashlib.sha256(b'nas-manager-demo-external').hexdigest()
CONTAINER=re.compile(r'[a-f0-9]{12,64}\Z')


UNIT_DIRECTORY=Path('/etc/systemd/system')


def _host_run(system,args,timeout=60):
    result=system.run(args,timeout=timeout,check=False)
    if result.returncode:raise SetupError('manager_command_failed')
    return result.stdout


def guarded_configuration(directory,record):
    """Derive the supervised config from the approved plan, never from inspect."""
    stage=directory/'manager-plans'/(record['name']+'.json')
    original=directory/'manager-projects'/record['name']/'compose.yml'
    for path in (stage,original):
        require(path.is_file() and path.resolve()==path and path.stat().st_uid==os.getuid() and not path.stat().st_mode&0o077,'unsafe_storage')
    plan=json.loads(stage.read_text())
    require(plan['name']==record['name'] and plan['record']['owner']==record['owner'] and plan['record']['services']==record['services'],'existing_installation')
    require(original.read_text()==compose_text(plan['config']),'existing_installation')
    config=plan['config']
    validate_supervised_lifecycle(config)
    for service in config['services'].values():service['restart']='no'
    return config


def validate_supervised_lifecycle(config):
    """Bound imports support long-running stacks only, restarted as one app.

    Every service must explicitly request always/unless-stopped. Compose's
    omitted/no/on-failure policies and successful one-shot init dependencies
    cannot be represented by this bounded supervisor and fail before writes.
    Named-volume-only imports retain native Docker lifecycle semantics.
    """
    for service in config['services'].values():
        require(service.get('restart','no') in ('always','unless-stopped'),'compose_lifecycle_unsupported')
        dependencies=service.get('depends_on',{})
        if isinstance(dependencies,dict):require(all(condition.get('condition')!='service_completed_successfully' for condition in dependencies.values()),'compose_lifecycle_unsupported')


def unit_quote(value):
    value=str(value);require(not any(ord(c)<32 for c in value),'unsafe_storage')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


def container_info(system,identity):
    require(isinstance(identity,str) and bool(CONTAINER.fullmatch(identity)),'container_invalid')
    # Intentionally exclude environment, command and arbitrary labels.
    fmt='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},"state":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"owner":{{json (index .Config.Labels "io.nas-manager.owner")}},"ports":{{json .NetworkSettings.Ports}},"mounts":{{json .Mounts}}}'
    raw=_host_run(system,['docker','inspect','--format',fmt,identity]);require(len(raw)<=65536,'container_too_large')
    return json.loads(raw)


def guard_record(system,record,allow_create=False):
    inventory=system.inventory()
    for identity,path in record['mounts'].items():require(str(volume_path(identity,inventory))==path,'storage_changed')
    config=record.get('config')
    services=config['services'] if config else {k:{'volumes':v['mounts']} for k,v in record['services'].items()}
    for service in services.values():
        for mount in service.get('volumes',[]):
            if mount['type']!='bind':continue
            path=Path(mount['source']);require(path.resolve()==path,'unsafe_storage')
            require(path.is_dir() or allow_create and str(path) in record.get('create',[]),'unsafe_storage')


class Manager:
    def __init__(self,engine):
        self.engine=engine;self.system=engine.system
        self.directory=engine.store.directory
        self.projects=self.directory/'manager-projects'
        require(not self.projects.is_symlink(),'unsafe_storage')
        self.projects.mkdir(mode=0o700,exist_ok=True)
        require(self.projects.stat().st_uid==os.getuid(),'unsafe_storage');self.projects.chmod(0o700)
        self.pending=self.directory/'manager-plans';require(not self.pending.is_symlink(),'unsafe_storage')
        self.pending.mkdir(mode=0o700,exist_ok=True)
        require(self.pending.stat().st_uid==os.getuid(),'unsafe_storage');self.pending.chmod(0o700)
        self.db=self.directory/'manager.sqlite';require(not self.db.is_symlink(),'unsafe_storage')
        self.plans={};self.plan_lock=threading.Lock()
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS projects(name TEXT PRIMARY KEY, body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS demo_state(name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            for identity,body in c.execute('SELECT id,body FROM jobs').fetchall():
                job=json.loads(body)
                if job['status']=='running':
                    job.update(status='interrupted',error='manager_interrupted')
                    for step in job.get('steps',[]):
                        if step['status']=='running':step['status']='interrupted'
                    c.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(job),identity))
        self.db.chmod(0o600)
        # A crash between staging a private plan and committing its job is
        # recoverable: publish an interrupted job, never silently replay it.
        with self.connect() as c:
            targets={json.loads(row[0]).get('target') for row in c.execute('SELECT body FROM jobs').fetchall()}
            for path in self.pending.glob('*.json'):
                require(not path.is_symlink(),'unsafe_storage')
                staged=json.loads(path.read_text());name=staged['name']
                if name not in targets:
                    job={'id':uuid.uuid4().hex,'status':'interrupted','operation':'deploy','target':name,'error':'manager_interrupted','steps':[{'id':'validate','status':'interrupted'},{'id':'pull','status':'pending'},{'id':'start','status':'pending'}]}
                    c.execute('INSERT INTO jobs VALUES(?,?,?)',(job['id'],json.dumps(job),time.time()))

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.db,timeout=10)
        try:
            c.execute('BEGIN IMMEDIATE');yield c;c.commit()
        except Exception:c.rollback();raise
        finally:c.close()

    def call(self,op,payload):
        require(isinstance(payload,dict) and set(payload)=={'session','data'},'operation_invalid')
        session=payload['session'];data=payload['data']
        require(isinstance(session,str) and 1<=len(session)<=256 and isinstance(data,dict),'operation_invalid')
        fields={'prepare_docker':set(),'catalog':set(),'options':set(),'overview':{'offset'},'plan':{'kind','app','volume','media','user','port','name','compose','variables'},
                'retry':{'id'},'deploy':{'plan_id'},'job':{'id'},'action':{'id','action'},'logs':{'id'}}
        require(op in fields and set(data)<=fields[op],'operation_invalid')
        try:
            if op=='prepare_docker':return self.prepare_docker()
            if op=='catalog':return {'apps':APPS}
            if op=='options':return self.options()
            if op=='overview':return self.overview(data.get('offset',0))
            if op=='plan':return self.plan(data,session)
            if op=='deploy':return self.deploy(data,session)
            if op=='retry':return self.retry(data)
            if op=='job':return self.job(data.get('id'))
            if op=='action':return self.action(data)
            if op=='logs':return self.logs(data)
        except SetupError:raise
        except (ValueError,TypeError,KeyError,AttributeError,RecursionError):raise SetupError('operation_invalid') from None
        except OSError:raise SetupError('manager_storage_failed') from None

    def _run(self,args,timeout=60):return _host_run(self.system,args,timeout)

    def docker(self):
        if self.system.demo:return True
        if not self.system.docker_available():return False
        try:return bool(self._run(['docker','info','--format','{{.ServerVersion}}'],timeout=10).strip())
        except SetupError:return False

    def options(self):
        inv=self.system.inventory();volumes=inv['volumes'];shares=[]
        if self.system.demo:
            media=Path(self.system.data)/'media'
            media.mkdir(exist_ok=True)
            users=[{'name':'nas-user','uid':os.getuid(),'gid':os.getgid()}]
            if volumes:shares=[{'name':'Media','path':str(media),'volume':volumes[0]['id']}]
        else:
            users=[{'name':u.pw_name,'uid':u.pw_uid,'gid':u.pw_gid} for u in pwd.getpwall() if 1000<=u.pw_uid<65534]
            try:
                mounts=json.loads(self._run(['omv-confdbadm','read','conf.system.filesystem.mountpoint']))
                records=json.loads(self._run(['omv-confdbadm','read','conf.system.sharedfolder']))
                for share in records:
                    mount=next((m for m in mounts if m.get('uuid')==share.get('mntentref')),None)
                    if not mount:continue
                    volume=next((v for v in volumes if v['path']==mount.get('dir')),None)
                    if not volume:continue
                    path=Path(volume['path'])/share['reldirpath']
                    if path.is_dir() and path.resolve()==path and path!=Path(volume['path']) and path.is_relative_to(Path(volume['path'])):
                        shares.append({'name':str(share['name'])[:100],'path':str(path),'volume':volume['id']})
            except (SetupError,ValueError,KeyError,TypeError):pass
        return {'volumes':volumes,'users':users[:128],'shares':shares[:128]}

    def prepare_docker(self):
        require(self.system.inventory().get('supported'),'unsupported_host')
        with self.engine.lock:
            # This selection exercises only the adapter's common host preflight;
            # install_docker does not install or configure Tailscale.
            selection=Selection(apps=['tailscale'])
            def work(phase):
                if phase=='validate':self.system.preflight(selection)
                else:self.system.install_docker(selection)
            return self._new_job('prepare_docker','docker',['validate','docker'],work)

    def _demo_state(self,value=None):
        with self.connect() as c:
            if value is not None:c.execute('INSERT OR REPLACE INTO demo_state VALUES(?,?)',('external',value))
            row=c.execute('SELECT value FROM demo_state WHERE name=?',('external',)).fetchone()
            return row[0] if row else 'running'

    def _demo_record(self,identity):
        if identity==DEMO_EXTERNAL:return None
        record=next((r for r in self._projects().values() if any(hashlib.sha256((r['name']+name).encode()).hexdigest()==identity for name in r['services'])),None)
        require(record is not None,'container_missing')
        return record

    def _projects(self):
        with self.connect() as c:return {name:json.loads(body) for name,body in c.execute('SELECT name,body FROM projects').fetchall()}

    def _save_project(self,record):
        with self.connect() as c:c.execute('INSERT OR REPLACE INTO projects VALUES(?,?)',(record['name'],json.dumps(record)))

    def _inspect(self,identity):return container_info(self.system,identity)

    @staticmethod
    def _owned(info,projects):
        record=projects.get(info.get('project'))
        if record and secrets.compare_digest(str(info.get('owner') or ''),record['owner']):
            config=record['services'].get(info.get('service'))
            if config and config['image']==info['image']:
                actual={(m['Source'],m['Destination'],m.get('RW',True)) for m in info.get('mounts',[]) if m.get('Type')=='bind'}
                expected={(m['source'],m['target'],not m.get('read_only',False)) for m in config['mounts'] if m['type']=='bind'}
                if actual==expected:return record
        return None

    def _legacy(self,info):
        project=info.get('project')
        if project not in ('nas-setup-immich','nas-setup-jellyfin'):return None
        app=project.removeprefix('nas-setup-')
        try:
            # Existing strict ownership check validates installation ID, image,
            # service and bind mounts, including stopped containers.
            self.system.check_project_ownership(app)
            return app
        except (SetupError,OSError,AttributeError):return None

    def _public(self,info,owned=False,legacy=False):
        ports=[]
        for container,bindings in (info.get('ports') or {}).items():
            number,protocol=container.split('/')
            for binding in bindings or []:
                if binding.get('HostPort','').isdigit():ports.append({'host':int(binding['HostPort']),'container':int(number),'protocol':protocol})
        return {'id':info['id'],'name':str(info['name']).lstrip('/')[:100],'image':str(info['image'])[:256],
            'state':info['state'],'status':info['state'],'project':str(info.get('project') or '')[:100],
            'managed':bool(owned or legacy),'action_scope':'app' if legacy or owned and owned.get('mounts') else 'container','actions':['start','stop','restart']+(['update'] if owned else []),
            'ports':ports[:32],'cpu':'—','memory':'—'}

    def overview(self,offset=0):
        require(type(offset) is int and 0<=offset<=100000,'operation_invalid')
        if not self.docker():return {'docker':'unavailable','demo':self.system.demo,'containers':[],'next_offset':None,'total':0}
        projects=self._projects();containers=[]
        if self.system.demo:
            for record in projects.values():
                for name,s in record['services'].items():
                    identity=hashlib.sha256((record['name']+name).encode()).hexdigest()
                    containers.append({'id':identity,'name':name,'image':s['image'],'state':record.get('states',{}).get(name,record.get('state','running')),
                        'status':record.get('states',{}).get(name,record.get('state','running')),'project':record['name'],'managed':True,'action_scope':'app' if record.get('mounts') else 'container',
                        'actions':['start','stop','restart','update'],'ports':s['ports'],'cpu':'0.0%','memory':'64 MiB'})
            containers.append({'id':DEMO_EXTERNAL,'name':'example-external','image':'nginx:1.28','state':self._demo_state(),
                'status':self._demo_state(),'project':'','managed':False,'action_scope':'container','actions':['start','stop','restart'],
                'ports':[{'host':18081,'container':80,'protocol':'tcp'}],'cpu':'0.0%','memory':'8 MiB'})
        else:
            all_ids=self._run(['docker','ps','-aq','--no-trunc']).split();total=len(all_ids)
            identities=all_ids[offset:offset+64]
            for identity in identities:
                try:
                    info=self._inspect(identity);containers.append(self._public(info,self._owned(info,projects),bool(self._legacy(info))))
                except SetupError:continue
            if identities:
                try:
                    stats=self._run(['docker','stats','--no-stream','--format','{{json .}}',*identities],timeout=20)
                    byid={c['id']:c for c in containers}
                    for line in stats.splitlines()[:128]:
                        item=json.loads(line);matches=[key for key in byid if key.startswith(item.get('ID','!'))]
                        if len(matches)==1:byid[matches[0]].update(cpu=str(item.get('CPUPerc','—'))[:30],memory=str(item.get('MemUsage','—'))[:60])
                except (SetupError,ValueError):pass
        if self.system.demo:
            total=len(containers);containers=containers[offset:offset+64]
        return {'docker':'available','demo':self.system.demo,'containers':containers,'total':total,'next_offset':offset+64 if offset+64<total else None}

    def _ports(self,config):
        seen=set()
        for service in config['services'].values():
            for value in service.get('ports',[]):
                p=port(value);key=(p['host'],p['protocol']);require(key not in seen,'port_conflict');seen.add(key)
                if not self.system.demo:
                    if p['protocol']=='tcp':self.system.check_port(p['host'])
                    else:
                        import socket
                        try:
                            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:probe.bind(('0.0.0.0',p['host']))
                        except OSError:raise SetupError('port_conflict') from None

    def _media_permission(self,path,user):
        if self.system.demo:return
        identity=['setpriv','--reuid',str(user['uid']),'--regid',str(user['gid']),'--clear-groups','--']
        result=self.system.run(identity+['test','-r',str(path)],check=False)
        result2=self.system.run(identity+['test','-w',str(path)],check=False)
        result3=self.system.run(identity+['test','-x',str(path)],check=False)
        require(not(result.returncode or result2.returncode or result3.returncode),'media_permission_denied')

    def plan(self,data,session):
        require(self.docker(),'docker_unavailable')
        kind=data.get('kind');inventory=self.system.inventory()
        roots=[volume_path(v['id'],inventory) for v in inventory['volumes']]
        create=[];mounts={};user=None
        if kind=='catalog':
            require(set(data)<={'kind','app','volume','media','user','port'},'operation_invalid')
            app=data.get('app');require(app in {a['id'] for a in APPS},'app_invalid')
            options=self.options();require(options['users'],'manager_user_required')
            user=next((u for u in options['users'] if u['name']==data.get('user')),None);require(user is not None,'manager_user_required')
            root=volume_path(data.get('volume'),inventory)
            media=None
            if app!='prowlarr':
                media=data.get('media');require(any(s['path']==media for s in options['shares']),'media_share_required')
                media=host_path(media,roots);self._media_permission(media,user)
            config_path=safe_directory(root,'.nas-manager',app,'config')
            require(not config_path.exists(),'existing_installation')
            selected_port=data.get('port',next(a['port'] for a in APPS if a['id']==app));require(type(selected_port) is int,'compose_port_invalid')
            config=render_catalog(app,config_path,media,user['uid'],user['gid'],selected_port)
            create=[str(config_path)];name=app;summary=preview(config)
        elif kind=='compose':
            require(set(data)<={'kind','name','compose','variables'},'operation_invalid')
            name=data.get('name');require(isinstance(name,str) and bool(SLUG.fullmatch(name)),'project_name_invalid')
            result=parse_compose(data.get('compose'),data.get('variables',{}),roots)
            if result['needs']:return {'valid':False,'needs':result['needs'],'summary':None}
            config=result['config'];summary=result['summary']
        else:raise SetupError('operation_invalid')
        if any(mount['type']=='bind' for service in config['services'].values() for mount in service.get('volumes',[])):validate_supervised_lifecycle(config)
        name='nas-manager-'+name;require(name not in self._projects(),'existing_installation')
        if not self.system.demo:
            require(not self._run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+name]).strip(),'existing_installation')
        self._ports(config)
        for service in config['services'].values():
            for mount in service.get('volumes',[]):
                if mount['type']=='bind':
                    root=next((r for r in roots if Path(mount['source']).is_relative_to(r)),None)
                    require(root is not None,'unsafe_storage')
                    volume=next(v for v in inventory['volumes'] if v['path']==str(root));mounts[volume['id']]=str(root)
        config['name']=name;summary['name']=name
        token=secrets.token_urlsafe(32)
        with self.plan_lock:
            self.plans={k:v for k,v in self.plans.items() if v['expires']>time.monotonic()}
            require(len(self.plans)<128,'manager_plan_limit')
            self.plans[token]={'session':session,'expires':time.monotonic()+900,'config':config,'name':name,'create':create,'mounts':mounts,'user':user}
        return {'valid':True,'needs':[],'plan_id':token,'summary':summary}

    def job(self,identity=None):
        require(identity is None or isinstance(identity,str) and bool(re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',identity)),'job_missing')
        with self.connect() as c:
            row=c.execute('SELECT body FROM jobs WHERE id=?',(identity,)).fetchone() if identity else c.execute('SELECT body FROM jobs ORDER BY created DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def busy(self):
        with self.connect() as c:
            return any(json.loads(row[0]).get('status')=='running' for row in c.execute('SELECT body FROM jobs').fetchall())

    def _idle(self):
        require(not self.busy(),'job_active')
        setup=self.engine.store.get()
        require(not setup or setup['status'] in ('succeeded','cancelled'),'job_active')

    def _new_job(self,operation,target,phases,work):
        self._idle()
        job={'id':uuid.uuid4().hex,'status':'running','operation':operation,'target':target,'error':None,
             'steps':[{'id':phase,'status':'pending'} for phase in phases]}
        with self.connect() as c:c.execute('INSERT INTO jobs VALUES(?,?,?)',(job['id'],json.dumps(job),time.time()))
        def run():
            try:
                for phase in phases:
                    self._phase(job['id'],phase,'running');work(phase);self._phase(job['id'],phase,'succeeded')
                self._change(job['id'],status='succeeded')
            except Exception as exc:
                self._phase(job['id'],phase,'failed')
                self._change(job['id'],status='failed',error=exc.code if isinstance(exc,SetupError) else 'manager_operation_failed')
        threading.Thread(target=run,daemon=True).start()
        return job

    def _change(self,identity,**changes):
        with self.connect() as c:
            job=json.loads(c.execute('SELECT body FROM jobs WHERE id=?',(identity,)).fetchone()[0]);job.update(changes)
            c.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(job),identity))

    def _phase(self,identity,phase,status):
        with self.connect() as c:
            job=json.loads(c.execute('SELECT body FROM jobs WHERE id=?',(identity,)).fetchone()[0])
            for step in job['steps']:
                if step['id']==phase:
                    step['status']=status
                    if status=='running':step['started_at']=time.time()
            c.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(job),identity))

    def _guard(self,record,allow_create=False):guard_record(self.system,record,allow_create)

    def _write(self,path,text):
        """Publish a complete private file without overwriting any existing file."""
        fd,temp=tempfile.mkstemp(prefix='.pending-',dir=path.parent)
        try:
            with os.fdopen(fd,'w') as stream:stream.write(text);stream.flush();os.fsync(stream.fileno())
            os.link(temp,path)
            directory=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:Path(temp).unlink(missing_ok=True)

    def _prepare_storage(self,plan):
        """Resume only directories covered by the immutable, root-private plan."""
        for folder in plan['create']:
            path=Path(folder);root=next(Path(p) for p in plan['mounts'].values() if path.is_relative_to(Path(p)))
            namespace=safe_directory(root,'.nas-manager')
            for parent in (namespace,namespace/path.parent.name):
                require(not parent.is_symlink(),'unsafe_storage')
                if parent.exists():require(parent.is_dir() and parent.stat().st_uid==os.getuid() and not parent.stat().st_mode&0o022,'unsafe_storage')
                else:parent.mkdir(mode=0o711)
            require(not path.is_symlink(),'unsafe_storage')
            if path.exists():
                expected=os.getuid() if self.system.demo else plan['user']['uid']
                require(path.is_dir(),'unsafe_storage')
                if path.stat().st_uid!=expected:
                    require(path.stat().st_uid==os.getuid() and not path.stat().st_mode&0o077 and not any(path.iterdir()),'unsafe_storage')
                    if not self.system.demo:os.chown(path,plan['user']['uid'],plan['user']['gid'])
            else:
                path.mkdir(mode=0o700)
                if not self.system.demo:os.chown(path,plan['user']['uid'],plan['user']['gid'])

    def _install_supervision(self,record):
        """Bind projects have one restart owner, independent of Docker startup."""
        config=guarded_configuration(self.directory,record)
        cfg=self.projects/record['name']/'guarded.yml';text=compose_text(config)
        if cfg.exists() or cfg.is_symlink():require(cfg.is_file() and cfg.resolve()==cfg and cfg.read_text()==text,'existing_installation')
        else:self._write(cfg,text)
        units=[]
        for root in sorted(set(record['mounts'].values())):
            unit=self._run(['systemd-escape','--path','--suffix=mount',root]).strip()
            require(bool(re.fullmatch(r'[A-Za-z0-9_.:\\-]+\.mount',unit)),'unsafe_storage');units.append(unit)
        unit_name=record['name']+'.service';target=UNIT_DIRECTORY/unit_name
        unit='[Unit]\nDescription=NAS Manager guarded application\nRequires=docker.service\nBindsTo={mounts}\nAfter=docker.service network-online.target {mounts}\nStartLimitIntervalSec=0\n[Service]\nType=simple\nExecStart=/opt/nas-setup/venv/bin/python -m nas_setup.manager --stack-run {directory} {name}\nExecStop=/usr/bin/docker compose --project-name {name} -f {config} stop\nRestart=always\nRestartSec=15\nTimeoutStopSec=120\nStandardOutput=null\nStandardError=null\n[Install]\nWantedBy=multi-user.target\n'.format(mounts=' '.join(units),directory=unit_quote(self.directory),name=record['name'],config=unit_quote(cfg))
        if target.exists() or target.is_symlink():require(target.is_file() and not target.is_symlink() and target.read_text()==unit,'existing_installation')
        else:self._write(target,unit)
        self._run(['systemctl','daemon-reload'])
        # Upgrade pre-release manager containers only after strict ownership
        # checks; external containers never have restart policies changed.
        identities=self._run(['docker','ps','-aq','--no-trunc','--filter','label=com.docker.compose.project='+record['name']]).split()
        for identity in identities:
            require(self._owned(self._inspect(identity),{record['name']:record}) is not None,'existing_installation')
            self._run(['docker','update','--restart=no',identity])
        return cfg,unit_name

    def _wait_supervised(self,record,cfg,timeout=180):
        config=guarded_configuration(self.directory,record)
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self._guard(record)
            identities=self._run(['docker','compose','--project-name',record['name'],'-f',str(cfg),'ps','-aq']).split()
            ready=set()
            for identity in identities:
                info=self._inspect(identity);require(self._owned(info,{record['name']:record}) is not None,'existing_installation')
                service=info['service']
                if info['state']=='running' and info.get('health','none') in ('none','healthy'):ready.add(service)
                elif info.get('health')=='unhealthy' or info['state']=='dead':raise SetupError('health_failed')
            if ready==set(record['services']):return
            time.sleep(2)
        raise SetupError('health_failed')

    def _start_supervised(self,record,recreate=False,action='start'):
        self._guard(record)
        cfg,unit=self._install_supervision(record)
        if action=='restart':
            self._run(['systemctl','stop',unit],timeout=180)
            self._run(['docker','compose','--project-name',record['name'],'-f',str(cfg),'stop'],timeout=180)
        if recreate:
            self._run(['systemctl','stop',unit],timeout=180)
            self._guard(record)
            self._run(['docker','compose','--project-name',record['name'],'-f',str(cfg),'create','--no-build'],timeout=180)
        self._run(['systemctl','enable',unit])
        self._run(['systemctl',action,unit],timeout=180)
        self._wait_supervised(record,cfg)

    def _deployment_work(self,plan):
        record=plan['record'];config=plan['config'];project=self.projects/plan['name']
        def work(phase):
            require(self.docker(),'docker_unavailable')
            if phase=='validate':
                self._guard(plan,True)
                if plan['user']:
                    for service in config['services'].values():
                        if len(service['volumes'])>1:self._media_permission(Path(service['volumes'][1]['source']),plan['user'])
                require(not project.is_symlink(),'unsafe_storage')
                if project.exists():require(project.stat().st_uid==os.getuid() and not project.stat().st_mode&0o077,'unsafe_storage')
                else:project.mkdir(mode=0o700)
                cfg=project/'compose.yml';text=compose_text(config)
                if cfg.exists() or cfg.is_symlink():require(not cfg.is_symlink() and cfg.is_file() and cfg.read_text()==text,'existing_installation')
                else:self._write(cfg,text)
                self._save_project(record)
                self._prepare_storage(plan)
                if not self.system.demo:self._run(['docker','compose','--project-name',record['name'],'-f',str(cfg),'config','--quiet'])
            elif phase=='pull':
                self._guard(record)
                if not self.system.demo:self._run(['docker','compose','--project-name',record['name'],'-f',str(project/'compose.yml'),'pull'],timeout=1800)
            elif phase=='start':
                self._guard(record)
                if not self.system.demo:
                    if record['mounts']:self._start_supervised(record,recreate=True)
                    else:self._run(['docker','compose','--project-name',record['name'],'-f',str(project/'compose.yml'),'up','-d','--no-build','--wait','--wait-timeout','180'],timeout=240)
        return work

    def deploy(self,data,session):
        token=data.get('plan_id');require(isinstance(token,str),'plan_expired')
        with self.engine.lock:
            self._idle()
            with self.plan_lock:
                plan=self.plans.get(token)
                require(plan is not None and plan['expires']>time.monotonic() and secrets.compare_digest(plan['session'],session),'plan_expired')
                self._guard(plan,True);self._ports(plan['config'])
                require(plan['name'] not in self._projects(),'existing_installation')
                config=plan['config'];owner=plan.setdefault('owner',secrets.token_hex(32))
                for service in config['services'].values():service['labels']={'io.nas-manager.owner':owner}
                project=self.projects/plan['name'];require(not project.exists() and not project.is_symlink(),'existing_installation')
                record={'name':plan['name'],'owner':owner,'mounts':plan['mounts'],'state':'running',
                    'services':{name:{'image':svc['image'],'mounts':svc.get('volumes',[]),'ports':[port(p) for p in svc.get('ports',[])]} for name,svc in config['services'].items()}}
                staged={key:plan[key] for key in ('name','config','mounts','create','user')};staged['record']=record
                stage=self.pending/(plan['name']+'.json');text=json.dumps(staged)
                if stage.exists() or stage.is_symlink():require(not stage.is_symlink() and stage.read_text()==text,'existing_installation')
                else:self._write(stage,text)
                # No app folders, Compose changes or jobs precede durable staging.
                job=self._new_job('deploy',plan['name'],['validate','pull','start'],self._deployment_work(staged))
                self.plans.pop(token)
                return job

    def retry(self,data):
        """Resume the root-private approved plan; never accept replacement config."""
        previous=self.job(data.get('id'))
        require(previous is not None and previous['status'] in ('failed','interrupted') and previous.get('operation') in ('deploy','retry'),'manager_retry_unavailable')
        with self.engine.lock:
            self._idle();stage=self.pending/(previous['target']+'.json')
            require(stage.is_file() and not stage.is_symlink(),'manager_retry_unavailable')
            plan=json.loads(stage.read_text());require(plan['name']==previous['target'],'unsafe_storage')
            self._guard(plan,True)
            return self._new_job('retry',plan['name'],['validate','pull','start'],self._deployment_work(plan))

    def action(self,data):
        identity=data.get('id');action=data.get('action')
        require(isinstance(identity,str) and bool(CONTAINER.fullmatch(identity)),'container_invalid')
        require(action in ('start','stop','restart','update'),'operation_invalid')
        require(self.docker(),'docker_unavailable')
        with self.engine.lock:
            self._idle();projects=self._projects()
            if self.system.demo:
                record=self._demo_record(identity);legacy=None
            else:
                info=self._inspect(identity);record=self._owned(info,projects);legacy=self._legacy(info)
            require(action!='update' or record is not None,'external_update_unsupported')
            def work(phase):
                if record and action!='stop':self._guard(record)
                if self.system.demo:
                    if phase=='apply':
                        state='exited' if action=='stop' else 'running'
                        if record:
                            if record.get('mounts') or action=='update':record['state']=state;record.pop('states',None)
                            else:
                                service=next(name for name in record['services'] if hashlib.sha256((record['name']+name).encode()).hexdigest()==identity)
                                record.setdefault('states',{})[service]=state
                            self._save_project(record)
                        else:self._demo_state(state)
                    return
                if legacy:
                    if action!='stop':self.system.guard_stack(legacy)
                    self._run(['systemctl',action,'nas-app-'+legacy+'.service'],timeout=180)
                elif record and record['mounts']:
                    if action=='stop':
                        cfg,unit=self._install_supervision(record)
                        self._run(['systemctl','disable',unit])
                        self._run(['systemctl','stop',unit],timeout=180)
                        # A pre-release project may never have had an active
                        # unit, so systemctl stop alone would skip ExecStop.
                        self._run(['docker','compose','--project-name',record['name'],'-f',str(cfg),'stop'],timeout=180)
                    elif action=='update' and phase=='pull':
                        self._run(['docker','compose','--project-name',record['name'],'-f',str(self.projects/record['name']/'compose.yml'),'pull'],timeout=1800)
                    else:self._start_supervised(record,recreate=action=='update',action='restart' if action=='restart' else 'start')
                elif action=='update':
                    cfg=self.projects/record['name']/'compose.yml';require(cfg.is_file() and not cfg.is_symlink(),'unsafe_storage')
                    args=['docker','compose','--project-name',record['name'],'-f',str(cfg)]
                    if phase=='pull':self._run(args+['pull'],timeout=1800)
                    else:self._run(args+['up','-d','--no-build','--wait','--wait-timeout','180'],timeout=240)
                else:self._run(['docker',action,identity],timeout=120)
            return self._new_job(action,identity,['pull','apply'] if action=='update' else ['apply'],work)

    def logs(self,data):
        identity=data.get('id');require(isinstance(identity,str) and bool(CONTAINER.fullmatch(identity)),'container_invalid')
        require(self.docker(),'docker_unavailable')
        if self.system.demo:
            self._demo_record(identity)
            return {'text':'Demo: application ready.\n'}
        self._inspect(identity)
        # Redact every environment value, not just keys that look secret. Values
        # are captured locally and never enter RPC results or diagnostics.
        fmt='{"env":{{json .Config.Env}},"cmd":{{json .Config.Cmd}},"entrypoint":{{json .Config.Entrypoint}},"healthcheck":{{json .Config.Healthcheck}}}'
        raw=self._run(['docker','inspect','--format',fmt,identity])
        require(len(raw)<=131072,'container_too_large')
        private=json.loads(raw)
        values=[item.partition('=')[2] for item in private.get('env') or [] if '=' in item]
        def arguments(value):
            if isinstance(value,str):
                values.append(value)
                for token in shlex.split(value):
                    values.append(token)
                    if '=' in token:values.append(token.partition('=')[2])
            elif isinstance(value,list):
                for item in value:arguments(item)
        arguments(private.get('cmd'));arguments(private.get('entrypoint'))
        arguments((private.get('healthcheck') or {}).get('Test'))
        result=self.system.run(['docker','logs','--tail','150','--since','24h',identity],check=False,timeout=15)
        require(result.returncode==0,'manager_command_failed')
        text=result.stdout+'\n'+result.stderr
        for value in sorted(set(values),key=len,reverse=True):
            if value:text=text.replace(value,'[redacted]')
        for record in self._projects().values():
            cfg=self.projects/record['name']/'compose.yml'
            if cfg.is_file() and not cfg.is_symlink():
                import yaml
                config=yaml.safe_load(cfg.read_text())
                for service in config.get('services',{}).values():
                    for value in service.get('environment',{}).values():
                        if value:text=text.replace(str(value).replace('$$','$'),'[redacted]')
        for cfg in getattr(self.system,'projects',self.projects).glob('*/db-password'):
            if not cfg.is_symlink():
                value=cfg.read_text().strip()
                if value:text=text.replace(value,'[redacted]')
        # Unknown secrets generated inside apps cannot be enumerated. Suppress
        # complete lines mentioning credentials or URLs, including login tokens.
        text='\n'.join('[sensitive log line redacted]' if re.search(r'(?i)pass|secret|token|credential|auth|api.?key|https?://',line) else line for line in text.splitlines())
        return {'text':text[-32768:]}


def run_stack(directory,name,system=None):
    """Root-only systemd entry point. Read-only state; no job recovery or replay."""
    directory=Path(directory)
    require(directory.is_absolute() and directory.resolve()==directory and directory.stat().st_uid==os.getuid(),'unsafe_storage')
    require(isinstance(name,str) and bool(re.fullmatch(r'nas-manager-[a-z][a-z0-9_-]{0,47}',name)),'project_name_invalid')
    db=directory/'manager.sqlite';require(db.is_file() and db.resolve()==db,'unsafe_storage')
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as connection:
        row=connection.execute('SELECT body FROM projects WHERE name=?',(name,)).fetchone()
    require(row is not None,'existing_installation');record=json.loads(row[0])
    require(record.get('mounts'),'unsafe_storage')
    if system is None:
        from .system import System
        system=System(directory)
    guard_record(system,record)
    config=guarded_configuration(directory,record)
    cfg=directory/'manager-projects'/name/'guarded.yml'
    require(cfg.is_file() and cfg.resolve()==cfg and cfg.read_text()==compose_text(config),'existing_installation')
    identities=_host_run(system,['docker','ps','-aq','--no-trunc','--filter','label=com.docker.compose.project='+name]).split()
    for identity in identities:
        require(Manager._owned(container_info(system,identity),{name:record}) is not None,'existing_installation')
    guard_record(system,record)
    os.execv('/usr/bin/docker',['docker','compose','--project-name',name,'-f',str(cfg),'up','--no-build','--no-recreate','--pull','never','--abort-on-container-exit','--no-color'])


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Root-only manager stack mount guard')
    parser.add_argument('--stack-run',nargs=2,metavar=('WORKER_DIRECTORY','PROJECT'),required=True)
    args=parser.parse_args()
    try:
        require(os.geteuid()==0,'root_required')
        run_stack(*args.stack_run)
    except Exception:
        # Never print configuration, app output or credentials to the journal.
        raise SystemExit(1) from None
