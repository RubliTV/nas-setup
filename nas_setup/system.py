"""System adapters and immutable app templates. Demo never executes commands."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import shutil
import re
import tempfile
import threading
import subprocess
import time
import urllib.request
import yaml
from .models import SetupError, Selection
from .progress import report
from .storage import volume_path, safe_directory

DATA=Path(__file__).parent/'data'
MANIFEST=json.loads((DATA/'manifest.json').read_text())

def write_secret(path):
    path=Path(path)
    if path.is_symlink():raise SetupError('unsafe_storage')
    if path.exists():
        value=path.read_text().strip()
        if path.stat().st_mode & 0o077 or not re.fullmatch(r'[a-f0-9]{64}',value):
            raise SetupError('unsafe_storage')
        return value
    value=secrets.token_hex(32)
    fd,name=tempfile.mkstemp(prefix='.secret-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            stream.write(value);stream.flush();os.fsync(stream.fileno())
        # Atomic publication without overwriting a credential created concurrently.
        try:os.link(name,path)
        except FileExistsError:return write_secret(path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:Path(name).unlink(missing_ok=True)
    return value

def bind(source,target,readonly=False):
    return {'type':'bind','source':str(source),'target':target,'read_only':readonly,'bind':{'create_host_path':False}}

def render_compose(app,selection,data,db,secret,owner=None):
    if app=='jellyfin':
        config={'services':{'jellyfin':{'image':'jellyfin/jellyfin:'+MANIFEST['jellyfin'],
            'environment':{'TZ':selection.timezone},'ports':['8096:8096'],
            'volumes':[bind(data/'config','/config'),bind(data/'cache','/cache'),bind(data/'media','/media',True)]}}}
    else:
        config=yaml.safe_load((DATA/'immich.yml').read_text())
        services=config['services'];server=services['immich-server']
        server['image']='ghcr.io/immich-app/immich-server:'+MANIFEST['immich']
        server['depends_on']={name:{'condition':'service_healthy'} for name in ('database','redis')}
        server['volumes']=[bind(data/'library','/data'),bind('/etc/localtime','/etc/localtime',True)]
        env={'DB_PASSWORD':secret,'DB_USERNAME':'postgres','DB_DATABASE_NAME':'immich','TZ':selection.timezone,
             'IMMICH_MACHINE_LEARNING_ENABLED':'true' if selection.machine_learning else 'false'}
        server['environment']=env
        database=services['database']
        database['environment'].update({'POSTGRES_PASSWORD':secret,'POSTGRES_USER':'postgres','POSTGRES_DB':'immich'})
        database['volumes']=[bind(db/'postgres','/var/lib/postgresql/data')]
        if not selection.machine_learning:
            services.pop('immich-machine-learning',None)
        else:
            ml=services['immich-machine-learning']
            ml['image']='ghcr.io/immich-app/immich-machine-learning:'+MANIFEST['immich']
            ml['environment']={'TZ':selection.timezone}
            ml['volumes']=[bind(data/'model-cache','/cache')]
        config.pop('volumes',None)
    config['name']='nas-setup-'+app
    for name,service in config['services'].items():
        service.pop('env_file',None);service.pop('container_name',None)
        # systemd is the only restart owner; every restart checks filesystem identity.
        service['restart']='no'
        service['labels']={'io.nas-setup.managed':'true'}
        if owner:service['labels']['io.nas-setup.owner']=owner
    return config

class DemoSystem:
    demo=True
    def __init__(self,directory,delay=.08):
        self.directory=Path(directory).resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.data=self.directory/'data';self.data.mkdir(exist_ok=True)
        self.db=self.directory/'ssd';self.db.mkdir(exist_ok=True)
        self.delay=delay;self.installed=set();self.disk_available=True

    def inventory(self):
        volumes=[{'id':'demo-data','path':str(self.data),'label':'Dados · 2 TB','free_gb':1432,'size_gb':1863,'fstype':'ext4','rotational':True},
                 {'id':'demo-ssd','path':str(self.db),'label':'SSD · 240 GB','free_gb':186,'size_gb':223,'fstype':'ext4','rotational':False}]
        return {'supported':True,'demo':True,'hostname':'nas-casa','os':'OMV 8 · Debian 13','cpu':'Intel Celeron J1800','cores':2,'memory_gb':8,
                'cpu_flags':['sse2'],'volumes':volumes if self.disk_available else [],'versions':MANIFEST,'omv_url':'http://192.168.1.50'}
    def preflight(self,selection):time.sleep(self.delay)
    def install_docker(self,selection):time.sleep(self.delay);self.installed.add('docker')
    def install_app(self,app,selection):time.sleep(self.delay);self.installed.add(app)
    def install_tailscale(self):time.sleep(self.delay);self.installed.add('tailscale')
    def tailscale_status(self):return {'state':'demo','url':None,'ips':[]}
    def verify(self,selection):
        report('checking_services')
        time.sleep(self.delay)
        return {'waiting':False,'links':{a:{'port':{'immich':2283,'jellyfin':8096}.get(a),'ready':True,'demo':True} for a in selection.apps}}
    def finish(self):pass

class System:
    demo=False
    def __init__(self,state_dir='/var/lib/nas-setup/worker'):
        self.state=Path(state_dir);self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.projects=self.state/'projects';self.projects.mkdir(exist_ok=True,mode=0o700)
        self.log_lock=threading.Lock()

    def run(self,args,timeout=60,check=True):
        env={**os.environ,'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','DEBIAN_FRONTEND':'noninteractive','LC_ALL':'C.UTF-8'}
        try:
            result=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  text=True,env=env,timeout=timeout)
        except subprocess.TimeoutExpired:
            self.diagnostic(Path(args[0]).name,'command timed out');raise SetupError('command_timeout')
        except OSError as exc:
            self.diagnostic(Path(args[0]).name,str(exc));raise SetupError('command_failed')
        if check and result.returncode:
            self.diagnostic(Path(args[0]).name,result.stdout+'\n'+result.stderr)
            raise SetupError('command_failed')
        return result

    def inventory(self):
        release={}
        try:
            for line in Path('/etc/os-release').read_text().splitlines():
                if '=' in line:
                    k,v=line.split('=',1);release[k]=v.strip('"')
            omv=self.run(['dpkg-query','-W','-f=${Version}','openmediavault'],check=False).stdout
            arch=self.run(['dpkg','--print-architecture']).stdout.strip()
            supported=release.get('ID')=='debian' and release.get('VERSION_ID')=='13' and omv.startswith('8.') and arch=='amd64'
        except (OSError,SetupError):supported=False;omv='';arch='unknown'
        cpu={}
        try:
            for line in Path('/proc/cpuinfo').read_text().splitlines():
                if ':' in line:
                    k,v=line.split(':',1);cpu.setdefault(k.strip(),v.strip())
            memory=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemTotal:')))/1048576
        except (OSError,StopIteration):memory=0
        volumes=[]
        if supported:
            conf=json.loads(self.run(['omv-confdbadm','read','conf.system.filesystem.mountpoint']).stdout)
            allowed={v['dir'] for v in conf}
            mounted=json.loads(self.run(['findmnt','--json','--list','--output','TARGET,SOURCE,FSTYPE,UUID,OPTIONS']).stdout)['filesystems']
            for mount in mounted:
                target=mount['target'];identity=mount.get('uuid')
                if target not in allowed or not identity or not target.startswith('/srv/') or 'rw' not in mount['options'].split(','):continue
                stats=os.statvfs(target)
                volumes.append({'id':identity,'path':target,'label':Path(target).name,'free_gb':round(stats.f_bavail*stats.f_frsize/2**30,1),
                    'size_gb':round(stats.f_blocks*stats.f_frsize/2**30,1),'fstype':mount['fstype'],'rotational':True})
        return {'supported':supported,'demo':False,'hostname':socket.gethostname(),'os':f'OMV {omv.strip()} · Debian {release.get("VERSION_ID","?")}',
            'cpu':cpu.get('model name','Unknown'),'cores':os.cpu_count() or 0,'memory_gb':round(memory,1),'cpu_flags':cpu.get('flags','').split(),
            'volumes':volumes,'versions':MANIFEST}

    def project(self,app):return self.projects/app

    def diagnostic(self,identity,message):
        message=str(message)
        for path in self.projects.glob('*/db-password'):
            secret=path.read_text().strip()
            if secret:message=message.replace(secret,'[redacted]')
        message=re.sub(r'https?://[^\s]+','[URL redacted]',message)
        message=re.sub(r'(?i)((?:password|token|secret|authkey)\s*[=:]\s*)[^\s,;]+',r'\1[redacted]',message)
        line=json.dumps({'time':time.time(),'operation':identity,'detail':message[-8192:]})+'\n'
        path=self.state/'diagnostics.log'
        with self.log_lock:
            if path.exists() and path.stat().st_size>262144:path.replace(self.state/'diagnostics.previous.log')
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(fd,'w') as stream:stream.write(line)

    def docker_available(self):return shutil.which('docker') is not None

    def check_project_ownership(self,app):
        if not self.docker_available():return False
        ids=self.run(['docker','ps','-aq','--filter','label=com.docker.compose.project=nas-setup-'+app]).stdout.split()
        if not ids:return False
        owner_path=self.state/'installation-id'
        if not owner_path.exists() or not (self.project(app)/'compose.yml').exists():raise SetupError('existing_installation')
        owner=write_secret(owner_path)
        containers=json.loads(self.run(['docker','inspect',*ids]).stdout)
        expected=yaml.safe_load((self.project(app)/'compose.yml').read_text())['services']
        running=False
        for container in containers:
            labels=container.get('Config',{}).get('Labels') or {}
            service=labels.get('com.docker.compose.service')
            if (labels.get('io.nas-setup.owner')!=owner or service not in expected
                or container.get('Config',{}).get('Image')!=expected[service]['image']):
                raise SetupError('existing_installation')
            # An owner label alone must not authorize changed storage bindings.
            actual={(m.get('Source'),m.get('Destination'),m.get('RW')) for m in container.get('Mounts',[]) if m.get('Type')=='bind'}
            desired={(v['source'],v['target'],not v.get('read_only',False)) for v in expected[service].get('volumes',[]) if v.get('type')=='bind'}
            if actual!=desired:raise SetupError('existing_installation')
            running=running or container.get('State',{}).get('Running',False)
        return running

    def selected_mounts(self,app,selection):
        inventory=self.inventory()
        ids={selection.volume}
        if app=='immich':ids.add(selection.database_volume)
        return {identity:str(volume_path(identity,inventory)) for identity in ids}

    def media_registration(self,record):
        mounts=json.loads(self.run(['omv-confdbadm','read','conf.system.filesystem.mountpoint']).stdout)
        shares=json.loads(self.run(['omv-confdbadm','read','conf.system.sharedfolder']).stdout)
        matching=[m for m in mounts if m.get('dir')==record['mount_path']]
        if len(matching)!=1:return False
        matching_shares=[item for item in shares if item.get('uuid')==record['share_uuid']]
        if len(matching_shares)!=1:return False
        share=matching_shares[0]
        return (share.get('mntentref')==matching[0]['uuid']
                and str(Path(record['mount_path'])/share['reldirpath'])==record['path'])

    def approve_media(self,resources):
        root=volume_path(resources['volume'],self.inventory())
        path=Path(resources['media_path'])
        matches=[share for share in resources['shares'] if share.get('folder')=='media' and share['path']==str(path)]
        if len(matches)!=1 or not matches[0].get('uuid'):raise SetupError('unsafe_storage')
        if not path.is_dir() or path.resolve()!=path or path==root or not path.is_relative_to(root):
            raise SetupError('unsafe_storage')
        record={'volume':resources['volume'],'mount_path':str(root),'path':str(path),'share_uuid':matches[0]['uuid']}
        if not self.media_registration(record):raise SetupError('storage_changed')
        project=self.project('jellyfin');project.mkdir(exist_ok=True,mode=0o700)
        target=project/'approved-media.json'
        if target.exists() and json.loads(target.read_text())!=record:raise SetupError('existing_installation')
        from .setup_engine import private_json
        private_json(target,record)

    def media_binding(self):
        target=self.project('jellyfin')/'approved-media.json'
        if not target.exists():return None
        record=json.loads(target.read_text())
        root=volume_path(record['volume'],self.inventory());path=Path(record['path'])
        if str(root)!=record['mount_path']:raise SetupError('storage_changed')
        if not path.is_dir() or path.resolve()!=path or path==root or not path.is_relative_to(root):
            raise SetupError('unsafe_storage')
        if not self.media_registration(record):raise SetupError('storage_changed')
        return path

    def guard_stack(self,app):
        project=self.project(app)
        selection=Selection(**json.loads((project/'selection.json').read_text()))
        expected=json.loads((project/'mounts.json').read_text())
        if self.selected_mounts(app,selection)!=expected:raise SetupError('storage_changed')
        # Resolve every persisted bind to catch symlinks introduced after installation.
        config=project/'compose.yml'
        if config.exists():
            for service in yaml.safe_load(config.read_text())['services'].values():
                for mount in service.get('volumes',[]):
                    source=Path(mount['source'])
                    if source==Path('/etc/localtime'):continue
                    if not source.exists() or source.resolve()!=source:raise SetupError('unsafe_storage')
                    if not any(source.is_relative_to(Path(root)/'.nas-setup') for root in expected.values()):
                        approved=self.media_binding() if app=='jellyfin' else None
                        if source!=approved or mount.get('target')!='/media' or mount.get('read_only') is not True:
                            raise SetupError('unsafe_storage')
        return expected

    def check_port(self,port):
        for family,address in [(socket.AF_INET,'0.0.0.0'),(socket.AF_INET6,'::')]:
            try:
                with socket.socket(family,socket.SOCK_STREAM) as probe:
                    if family==socket.AF_INET6:probe.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
                    probe.bind((address,port))
            except OSError as exc:
                import errno
                if exc.errno not in (errno.EAFNOSUPPORT,errno.EADDRNOTAVAIL,errno.EPROTONOSUPPORT):
                    raise SetupError('port_conflict')

    def preflight(self,selection):
        if not self.inventory()['supported']:raise SetupError('unsupported_host')
        if os.statvfs('/').f_bavail*os.statvfs('/').f_frsize<8*2**30:raise SetupError('system_disk_full')
        for app,port in [('immich',2283),('jellyfin',8096)]:
            if app not in selection.apps:continue
            saved=self.project(app)/'selection.json'
            if saved.exists():
                previous=Selection(**json.loads(saved.read_text()))
                if previous.model_dump()!=selection.model_dump():raise SetupError('existing_installation')
                self.guard_stack(app)
            if not self.check_project_ownership(app):self.check_port(port)
        if any(app in selection.apps for app in ('immich','jellyfin')):
            docker=self.run(['dpkg-query','-W','-f=${Status}','docker.io'],check=False)
            if docker.returncode==0 and 'install ok installed' in docker.stdout:raise SetupError('docker_conflict')

    def apt(self,*packages):
        self.run(['apt-get','-o','DPkg::Lock::Timeout=60','--no-remove','--yes','install',*packages],timeout=1800)

    def install_docker(self,selection):
        report('installing_docker')
        ready=self.run(['dpkg-query','-W','-f=${Status}','openmediavault-omvextrasorg'],check=False)
        if 'install ok installed' not in ready.stdout:
            try:
                with urllib.request.urlopen(MANIFEST['omv_extras_url'],timeout=60) as r:content=r.read(5*1024*1024)
            except OSError:raise SetupError('download_failed')
            if hashlib.sha256(content).hexdigest()!=MANIFEST['omv_extras_sha256']:raise SetupError('checksum_failed')
            package=self.state/'omv-extras.deb';package.write_bytes(content);package.chmod(0o600)
            self.run(['apt-get','update'],timeout=600);self.apt(str(package))
        self.run(['omv-rpc','-u','admin','omvextras','set','{"docker":true}'],timeout=600)
        self.run(['apt-get','update'],timeout=600)
        self.apt('openmediavault-compose','docker-ce','docker-compose-plugin')
        self.run(['systemctl','enable','--now','docker'],timeout=120)
        self.run(['docker','compose','version'])
        self.run(['docker','info'],timeout=30)

    def _storage(self,app,selection):
        inv=self.inventory();base=volume_path(selection.volume,inv)
        dbbase=volume_path(selection.database_volume,inv) if app=='immich' else base
        for volume in {base,dbbase}:
            namespace=safe_directory(volume,'.nas-setup')
            if namespace.exists():
                st=namespace.stat()
                if st.st_uid!=0 or st.st_mode&0o077 or not (namespace/'.owned').is_file():raise SetupError('existing_installation')
            else:
                namespace.mkdir(mode=0o700);(namespace/'.owned').write_text('nas-setup-v1\n')
        data=safe_directory(base,'.nas-setup',app)
        db=safe_directory(dbbase,'.nas-setup',app+'-db')
        for path in [data,db]:path.mkdir(exist_ok=True,mode=0o700)
        for name in (['library','model-cache'] if app=='immich' else ['config','cache','media']):
            safe_directory(data,name).mkdir(exist_ok=True,mode=0o700)
        if app=='immich':safe_directory(db,'postgres').mkdir(exist_ok=True,mode=0o700)
        return data,db

    def install_app(self,app,selection):
        self.preflight(selection)
        project=self.project(app);project.mkdir(exist_ok=True,mode=0o700)
        mounts=self.selected_mounts(app,selection)
        record=project/'mounts.json'
        if record.exists() and json.loads(record.read_text())!=mounts:raise SetupError('storage_changed')
        record.write_text(json.dumps(mounts));record.chmod(0o600)
        data,db=self._storage(app,selection)
        secret=write_secret(project/'db-password') if app=='immich' else ''
        compose=render_compose(app,selection,data,db,secret,write_secret(self.state/'installation-id'))
        if app=='jellyfin':
            media=self.media_binding()
            if media:
                compose['services']['jellyfin']['volumes']=[v for v in compose['services']['jellyfin']['volumes'] if v['target']!='/media']+[bind(media,'/media',True)]
        cfg=project/'compose.yml';cfg.write_text(yaml.safe_dump(compose,sort_keys=False));cfg.chmod(0o600)
        saved=project/'selection.json';saved.write_text(selection.model_dump_json());saved.chmod(0o600)
        self.run(['docker','compose','-f',str(cfg),'config','--quiet'])
        report('downloading_app')
        self.run(['docker','compose','-f',str(cfg),'pull'],timeout=1800)
        # Recheck after slow downloads, immediately before enabling the stack.
        self.guard_stack(app)
        self.check_project_ownership(app)
        mounts={volume_path(selection.volume,self.inventory())}
        if app=='immich':mounts.add(volume_path(selection.database_volume,self.inventory()))
        units=[self.run(['systemd-escape','--path','--suffix=mount',str(m)]).stdout.strip() for m in mounts]
        unit='''[Unit]
Description=NAS Setup managed application
After=docker.service network-online.target
Requires=docker.service
BindsTo={units}
After={units}
StartLimitIntervalSec=0
[Service]
Type=simple
ExecStart=/opt/nas-setup/venv/bin/nas-setup stack-run {app}
ExecStop=/usr/bin/docker compose -f {cfg} stop
Restart=on-failure
RestartSec=15
TimeoutStopSec=120
[Install]
WantedBy=multi-user.target
'''.format(units=' '.join(units),app=app,cfg=cfg)
        Path('/etc/systemd/system/nas-app-'+app+'.service').write_text(unit)
        self.run(['systemctl','daemon-reload'])
        report('starting_app')
        self.run(['systemctl','enable','--now','nas-app-'+app+'.service'],timeout=120)
        report('checking_app')
        self.wait_app(app,timeout=900 if app=='immich' else 300)

    def prepare_immich_database(self):
        from .immich import prepare_database
        prepare_database(self)

    def stack_run(self,app):
        if app not in ('immich','jellyfin'):raise SetupError('operation_invalid')
        project=self.project(app);selection=Selection(**json.loads((project/'selection.json').read_text()))
        self.guard_stack(app)
        self.check_project_ownership(app)
        if app=='immich':self.prepare_immich_database()
        # Foreground Compose exits if a component dies. systemd reruns this guard before every restart.
        os.execv('/usr/bin/docker',['docker','compose','-f',str(project/'compose.yml'),'up','--no-build','--no-recreate','--abort-on-container-exit'])

    def wait_app(self,app,timeout=300):
        endpoint={'immich':'http://127.0.0.1:2283/api/server/ping','jellyfin':'http://127.0.0.1:8096/health'}[app]
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                with urllib.request.urlopen(endpoint,timeout=3) as response:
                    if response.status==200:return
            except OSError:pass
            time.sleep(2)
        raise SetupError('health_failed')

    def tailscale_status(self):
        try:data=json.loads(self.run(['tailscale','status','--json'],timeout=10,check=False).stdout)
        except (SetupError,ValueError):return {'state':'not_installed','url':None,'ips':[]}
        url=data.get('AuthURL') or None
        if url and not url.startswith('https://login.tailscale.com/'):url=None
        return {'state':data.get('BackendState','unknown'),'url':url,'ips':data.get('TailscaleIPs',[])}

    def install_tailscale(self):
        report('installing_tailscale')
        if self.tailscale_status()['state']=='not_installed':
            for remote,local in [('trixie.noarmor.gpg','/usr/share/keyrings/tailscale-archive-keyring.gpg'),
                                 ('trixie.tailscale-keyring.list','/etc/apt/sources.list.d/tailscale.list')]:
                try:
                    with urllib.request.urlopen('https://pkgs.tailscale.com/stable/debian/'+remote,timeout=30) as r:content=r.read(1024*1024)
                except OSError:raise SetupError('download_failed')
                target=Path(local)
                if target.exists() and target.read_bytes()!=content:raise SetupError('existing_installation')
                target.write_bytes(content);target.chmod(0o644)
            self.run(['apt-get','update'],timeout=600);self.apt('tailscale')
        self.run(['systemctl','enable','--now','tailscaled'],timeout=60)
        state=self.tailscale_status()['state']
        if state=='NeedsLogin':self.run(['tailscale','up','--timeout=15s'],timeout=25,check=False)
        elif state not in ('Running','NeedsMachineAuth'):raise SetupError('tailscale_existing_config')

    def verify(self,selection):
        report('checking_services')
        links={}
        for app in selection.apps:
            if app=='tailscale':continue
            self.wait_app(app,timeout=30);links[app]={'port':2283 if app=='immich' else 8096,'ready':True}
        waiting=False
        if 'tailscale' in selection.apps:
            status=self.tailscale_status();waiting=status['state']!='Running'
            links['tailscale']={'ready':not waiting,'ips':status['ips']}
        return {'links':links,'waiting':waiting}

    def finish(self):
        import threading
        def disable():self.run(['systemctl','disable','--now','nas-setup-web.service','nas-setup-worker.service'],timeout=30,check=False)
        threading.Timer(2,disable).start()
