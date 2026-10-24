"""System adapters and immutable app templates. Demo never executes commands."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.request
import yaml
from .models import SetupError, Selection
from .storage import volume_path, safe_directory

DATA=Path(__file__).parent/'data'
MANIFEST=json.loads((DATA/'manifest.json').read_text())

def write_secret(path):
    path=Path(path)
    if path.is_symlink():raise SetupError('unsafe_storage')
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        if path.stat().st_mode & 0o077:raise SetupError('unsafe_storage')
        return path.read_text().strip()
    value=secrets.token_hex(32)
    with os.fdopen(fd,'w') as f:f.write(value)
    return value

def bind(source,target,readonly=False):
    return {'type':'bind','source':str(source),'target':target,'read_only':readonly,'bind':{'create_host_path':False}}

def render_compose(app,selection,data,db,secret):
    if app=='jellyfin':
        config={'services':{'jellyfin':{'image':'jellyfin/jellyfin:'+MANIFEST['jellyfin'],
            'environment':{'TZ':selection.timezone},'ports':['8096:8096'],
            'volumes':[bind(data/'config','/config'),bind(data/'cache','/cache'),bind(data/'media','/media',True)]}}}
    else:
        config=yaml.safe_load((DATA/'immich.yml').read_text())
        services=config['services'];server=services['immich-server']
        server['image']='ghcr.io/immich-app/immich-server:'+MANIFEST['immich']
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
        time.sleep(self.delay)
        return {'waiting':False,'links':{a:{'port':{'immich':2283,'jellyfin':8096}.get(a),'ready':True,'demo':True} for a in selection.apps}}
    def finish(self):pass

class System:
    demo=False
    def __init__(self,state_dir='/var/lib/nas-setup/worker'):
        self.state=Path(state_dir);self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.projects=self.state/'projects';self.projects.mkdir(exist_ok=True,mode=0o700)

    def run(self,args,timeout=60,check=True):
        env={**os.environ,'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','DEBIAN_FRONTEND':'noninteractive','LC_ALL':'C.UTF-8'}
        try:
            result=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  text=True,env=env,timeout=timeout)
        except subprocess.TimeoutExpired:raise SetupError('command_timeout')
        except OSError:raise SetupError('command_failed')
        if check and result.returncode:raise SetupError('command_failed')
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

    def preflight(self,selection):
        if not self.inventory()['supported']:raise SetupError('unsupported_host')
        if os.statvfs('/').f_bavail*os.statvfs('/').f_frsize<8*2**30:raise SetupError('system_disk_full')
        # All package manipulation uses apt's own bounded locking; never delete locks.
        for app,port in [('immich',2283),('jellyfin',8096)]:
            if app not in selection.apps:continue
            saved=self.project(app)/'selection.json'
            if saved.exists():
                previous=Selection(**json.loads(saved.read_text()))
                if previous.model_dump()!=selection.model_dump():raise SetupError('existing_installation')
            else:
                with socket.socket() as probe:
                    if probe.connect_ex(('127.0.0.1',port))==0:raise SetupError('port_conflict')
        docker=self.run(['dpkg-query','-W','-f=${Status}','docker.io'],check=False)
        if docker.returncode==0 and 'install ok installed' in docker.stdout:raise SetupError('docker_conflict')

    def apt(self,*packages):
        self.run(['apt-get','-o','DPkg::Lock::Timeout=60','--no-remove','--yes','install',*packages],timeout=1800)

    def install_docker(self,selection):
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
        data,db=self._storage(app,selection)
        secret=write_secret(project/'db-password') if app=='immich' else ''
        compose=render_compose(app,selection,data,db,secret)
        cfg=project/'compose.yml';cfg.write_text(yaml.safe_dump(compose,sort_keys=False));cfg.chmod(0o600)
        saved=project/'selection.json';saved.write_text(selection.model_dump_json());saved.chmod(0o600)
        self.run(['docker','compose','-f',str(cfg),'config','--quiet'])
        self.run(['docker','compose','-f',str(cfg),'pull'],timeout=1800)
        # Recheck after slow downloads, immediately before enabling the stack.
        volume_path(selection.volume,self.inventory())
        if app=='immich':volume_path(selection.database_volume,self.inventory())
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
        self.run(['systemctl','enable','--now','nas-app-'+app+'.service'],timeout=120)
        self.wait_app(app)

    def stack_run(self,app):
        if app not in ('immich','jellyfin'):raise SetupError('operation_invalid')
        project=self.project(app);selection=Selection(**json.loads((project/'selection.json').read_text()))
        volume_path(selection.volume,self.inventory())
        if app=='immich':volume_path(selection.database_volume,self.inventory())
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
