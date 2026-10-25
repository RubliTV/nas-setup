"""Live manager recipe smoke: ONLY the fixed, guarded disposable OMV VM.

Uses existing storage/share/user, an isolated worker directory, and three new
manager projects. Never formats disks, removes volumes, or alters existing apps.
Run with the candidate package on PYTHONPATH and the installed venv interpreter.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import socket
import stat
import subprocess
import time
import urllib.error
import urllib.request

STATE=Path('/root/nas-v030-manager-test')
EVENTS=[]


def emit(event,**details):
    record={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':event,**details}
    EVENTS.append(record);print(json.dumps(record,sort_keys=True),flush=True)


def run(args,timeout=60):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:raise RuntimeError('Smoke command failed: '+args[0])
    return result.stdout


def guard():
    if os.geteuid()!=0 or platform.system()!='Linux' or socket.gethostname()!='nas-v02-vm':
        raise SystemExit('Dedicated disposable OMV VM required')
    marker=Path('/root/NAS_SETUP_DISPOSABLE_VM')
    if not marker.is_file() or marker.is_symlink() or marker.stat().st_uid!=0:
        raise SystemExit('Disposable marker invalid')
    disks=[d for d in json.loads(run(['lsblk','--json','--bytes','--output','TYPE,SIZE,MODEL,SERIAL,TRAN']))['blockdevices'] if d['type']=='disk']
    expected={'NASV02SYSTEM','NASV02DATA1','NASV02DATA2','NASV02DATA3','NASV02DATA4'}
    if len(disks)!=5 or {d['serial'] for d in disks}!=expected:raise SystemExit('Unexpected disks')
    if not all(d['size']==16*1024**3 and d['model'].strip()=='QEMU HARDDISK' and d['tran']=='sata' for d in disks if d['serial'].startswith('NASV02DATA')):
        raise SystemExit('Unexpected synthetic data disk geometry')
    emit('guards_pass',hostname=socket.gethostname(),serials=sorted(expected))


def main():
    guard()
    from nas_setup.engine import Engine
    from nas_setup.system import System
    from nas_setup.catalog import APPS
    from nas_setup.models import SetupError
    if STATE.is_symlink():raise SystemExit('Unsafe isolated state directory')
    STATE.mkdir(mode=0o700,exist_ok=True)
    baseline={c['Id']:c['Name'] for c in json.loads(run(['docker','inspect',*run(['docker','ps','-aq']).split()]))} if run(['docker','ps','-aq']).strip() else {}
    baseline={k:v for k,v in baseline.items() if not v.lstrip('/').startswith(('nas-manager-','nas-v030-external-smoke'))}
    config=Path('/etc/openmediavault/config.xml');xml_before=hashlib.sha256(config.read_bytes()).hexdigest()
    system=System(STATE);engine=Engine(STATE,system);manager=engine.manager
    session='disposable-manager-smoke'
    def api(op,data=None):return engine.call('manager_'+op,{'session':session,'data':data or {}})
    inv=system.inventory();assert inv['supported'] and not inv['demo']
    options=api('options');user=next(u for u in options['users'] if u['name']=='smokeana')
    share=next(s for s in options['shares'] if s['name']=='SmokeFiles')
    volume=next(v for v in options['volumes'] if v['id']==share['volume'])
    share_before=(Path(share['path']).stat().st_uid,Path(share['path']).stat().st_gid,stat.S_IMODE(Path(share['path']).stat().st_mode))
    emit('live_inventory',docker=run(['docker','version','--format','{{.Server.Version}}']).strip(),volume=volume['id'],user=user['name'],uid=user['uid'],gid=user['gid'],share=share['name'],preexisting_containers=len(baseline))
    def wait(job,expect='succeeded'):
        deadline=time.monotonic()+2400;previous=None;heartbeat=0
        while True:
            job=api('job',{'id':job['id']})
            phase=next((s['id'] for s in job['steps'] if s['status']=='running'),job['status'])
            if phase!=previous or time.monotonic()-heartbeat>30:
                emit('job_progress',id=job['id'],operation=job['operation'],target=job['target'],phase=phase,status=job['status'],error=job.get('error'))
                previous=phase;heartbeat=time.monotonic()
            if job['status']!='running':
                if job['status']!=expect:raise RuntimeError('Job ended '+job['status']+': '+str(job.get('error')))
                return job
            if time.monotonic()>deadline:raise TimeoutError('Manager job exceeded 40 minutes')
            time.sleep(3)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def http_ready(port):
        deadline=time.monotonic()+600
        while True:
            try:
                with opener.open('http://127.0.0.1:'+str(port)+'/',timeout=10) as response:
                    if response.status==200:
                        body=response.read(262144)
                        assert b'<html' in body.lower() or b'<!doctype' in body.lower()
                        return response.status
            except (urllib.error.URLError,TimeoutError,ConnectionError):pass
            if time.monotonic()>deadline:raise TimeoutError('Application HTTP readiness timeout on port '+str(port))
            time.sleep(3)
    results=[]
    for app in APPS:
        name='nas-manager-'+app['id']
        if name not in manager._projects():
            data={'kind':'catalog','app':app['id'],'volume':volume['id'],'user':user['name'],'port':app['port']}
            if app['id']!='prowlarr':data['media']=share['path']
            plan=api('plan',data);assert plan['valid']
            emit('plan_valid',app=app['id'],image=app['image'],port=app['port'])
            job=wait(api('deploy',{'plan_id':plan['plan_id']}))
        else:
            with manager.connect() as db:
                jobs=[json.loads(row[0]) for row in db.execute('SELECT body FROM jobs ORDER BY created DESC')]
            prior=next(j for j in jobs if j['target']==name and j['operation'] in ('deploy','retry'))
            if prior['status'] in ('failed','interrupted'):job=wait(api('retry',{'id':prior['id']}))
            else:assert prior['status']=='succeeded';job=prior
        status=http_ready(app['port'])
        identity=run(['docker','ps','-q','--no-trunc','--filter','label=com.docker.compose.project='+name]).strip()
        assert len(identity)==64
        public=next(c for c in api('overview')['containers'] if c['id']==identity)
        assert public['managed'] and public['state']=='running' and public['image']==app['image']
        private_config=STATE/'manager-projects'/name/'compose.yml'
        assert stat.S_IMODE(private_config.stat().st_mode)==0o600
        app_config=Path(volume['path'])/'.nas-manager'/app['id']/'config'
        assert app_config.stat().st_uid==user['uid'] and app_config.stat().st_gid==user['gid']
        assert stat.S_IMODE(app_config.stat().st_mode)==0o700
        # Verify the actual application process identity, not just environment PUID.
        processes=run(['docker','top',identity,'-eo','pid,uid,gid,args'])
        app_lines=[line.split(None,3) for line in processes.splitlines()[1:] if '/app/'+app['name'] in line or '/app/'+app['id'] in line]
        assert app_lines and all(line[1:3]==[str(user['uid']),str(user['gid'])] for line in app_lines)
        run(['docker','exec','--user',str(user['uid'])+':'+str(user['gid']),identity,'test','-w','/config'])
        if app['id']!='prowlarr':run(['docker','exec','--user',str(user['uid'])+':'+str(user['gid']),identity,'test','-w','/data'])
        result={'app':app['id'],'image':app['image'],'port':app['port'],'http_status':status,'uid':user['uid'],'gid':user['gid'],'config_mode':'0700','job_status':job['status']}
        results.append(result);emit('app_pass',**result)
    external='nas-v030-external-smoke'
    found=run(['docker','ps','-aq','--filter','name=^/'+external+'$']).strip()
    if not found:
        found=run(['docker','run','-d','--name',external,'--entrypoint','/bin/sh',APPS[0]['image'],'-c','echo manager-smoke-started; while true; do sleep 30; done']).strip()
    identity=run(['docker','inspect','--format','{{.Id}}',found]).strip()
    for action in ('stop','start','restart'):
        job=wait(api('action',{'id':identity,'action':action}))
        state=run(['docker','inspect','--format','{{.State.Status}}',identity]).strip()
        assert state==('exited' if action=='stop' else 'running')
        emit('external_action_pass',action=action,state=state)
    logs=api('logs',{'id':identity})
    assert isinstance(logs['text'],str) and logs['text']
    emit('external_logs_pass',bytes=len(logs['text'].encode()))
    try:api('action',{'id':identity,'action':'update'})
    except SetupError as exc:assert exc.code=='external_update_unsupported'
    else:raise AssertionError('External recreation incorrectly allowed')
    emit('external_update_rejected')
    assert hashlib.sha256(config.read_bytes()).hexdigest()==xml_before
    assert share_before==(Path(share['path']).stat().st_uid,Path(share['path']).stat().st_gid,stat.S_IMODE(Path(share['path']).stat().st_mode))
    current=json.loads(run(['docker','inspect',*baseline])) if baseline else []
    assert {c['Id']:c['Name'] for c in current}==baseline
    emit('existing_config_share_and_containers_preserved')
    payload={'disposable_vm_only':True,'physical_nas_tested':False,'apps':results,'events':EVENTS}
    (STATE/'evidence.json').write_text(json.dumps(payload,indent=2)+'\n')
    emit('MANAGER_SMOKE_PASS')


if __name__=='__main__':
    try:main()
    except Exception as error:
        emit('MANAGER_SMOKE_FAILED',kind=type(error).__name__,code=getattr(error,'code',str(error) if isinstance(error,(AssertionError,RuntimeError,TimeoutError)) else 'unexpected_error'))
        raise SystemExit(1) from None
