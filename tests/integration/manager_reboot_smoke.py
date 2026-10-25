"""Before/after one real reboot of the exact guarded disposable OMV VM.

The operator reboots between modes; this script never detaches storage or powers
off a host. The existing Immich sample must remain stopped throughout.
"""
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

from manager_smoke import guard, emit, run, EVENTS

STATE=Path('/root/nas-v030-manager-test')


def main(mode):
    guard()
    from nas_setup.engine import Engine
    from nas_setup.system import System
    import nas_setup.manager as module
    assert Path(module.__file__).is_relative_to(Path('/opt/nas-setup/venv'))
    engine=Engine(STATE,System(STATE))
    def api(op,data=None):return engine.call('manager_'+op,{'session':'reboot-smoke','data':data or {}})
    def identity(app):
        ids=run(['docker','ps','-aq','--no-trunc','--filter','label=com.docker.compose.project=nas-manager-'+app]).split()
        assert len(ids)==1
        return ids[0]
    def state(app):return run(['docker','inspect','--format','{{.State.Status}}',identity(app)]).strip()
    def prop(app,key):return run(['systemctl','show','nas-manager-'+app+'.service','-p',key,'--value']).strip()
    def action(app,verb):
        job=api('action',{'id':identity(app),'action':verb});deadline=time.monotonic()+300
        while job['status']=='running':
            if time.monotonic()>deadline:raise TimeoutError('Manager action deadline')
            time.sleep(2);job=api('job',{'id':job['id']})
        assert job['status']=='succeeded'
        emit('reboot_manager_action_pass',app=app,action=verb)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def ready(app,port):
        deadline=time.monotonic()+600
        while True:
            try:
                with opener.open('http://127.0.0.1:'+str(port)+'/',timeout=10) as response:
                    if response.status==200:break
            except (urllib.error.URLError,ConnectionError,TimeoutError):pass
            if time.monotonic()>deadline:raise TimeoutError('Reboot HTTP readiness deadline')
            time.sleep(3)
        assert prop(app,'ActiveState')=='active' and prop(app,'UnitFileState')=='enabled'
        assert state(app)=='running'
        emit('reboot_app_ready',app=app,http_status=200,unit_enabled=True,unit_active=True)
    def immich():
        assert run(['systemctl','show','nas-app-immich.service','-p','UnitFileState','--value']).strip() in ('','disabled','masked','not-found')
        ids=run(['docker','ps','-aq','--no-trunc','--filter','label=com.docker.compose.project=nas-setup-immich']).split()
        assert len(ids)==3
        result={i:run(['docker','inspect','--format','{{.State.Status}}',i]).strip() for i in ids}
        assert set(result.values())=={'exited'}
        assert all(run(['docker','inspect','--format','{{.HostConfig.RestartPolicy.Name}}',i]).strip()=='no' for i in ids)
        return result
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    before_path=STATE/'reboot-before.json'
    if mode=='before':
        assert not before_path.exists(), 'Exactly one reboot test is permitted'
        action('prowlarr','stop')
        assert state('prowlarr')=='exited' and prop('prowlarr','UnitFileState')=='disabled'
        assert prop('prowlarr','ActiveState')=='inactive'
        ready('radarr',7878);ready('sonarr',8989)
        before_path.write_text(json.dumps({'boot_id':boot,'immich':immich(),'events':EVENTS},indent=2)+'\n')
        before_path.chmod(0o600)
        emit('REBOOT_PRECONDITIONS_PASS',boot_id=boot,prowlarr_disabled=True)
    elif mode=='after':
        before=json.loads(before_path.read_text());assert boot!=before['boot_id']
        assert state('prowlarr')=='exited' and prop('prowlarr','UnitFileState')=='disabled'
        assert prop('prowlarr','ActiveState')=='inactive'
        emit('explicit_stop_survived_reboot',app='prowlarr',unit_disabled=True,container_state='exited')
        ready('radarr',7878);ready('sonarr',8989)
        assert immich()==before['immich']
        action('prowlarr','start');ready('prowlarr',9696)
        assert immich()==before['immich']
        emit('MANAGER_REBOOT_SMOKE_PASS',prior_boot_id=before['boot_id'],boot_id=boot,prior_immich_preserved=True)
        (STATE/'reboot-evidence.json').write_text(json.dumps({'disposable_vm_only':True,'physical_nas_tested':False,'real_reboot_tested':True,'storage_detached':False,'before':before,'events':EVENTS},indent=2)+'\n')
    else:raise ValueError('Expected before or after')


if __name__=='__main__':
    try:main(sys.argv[1])
    except Exception as error:
        import traceback
        frame=traceback.extract_tb(error.__traceback__)[-1]
        emit('MANAGER_REBOOT_SMOKE_FAILED',location=Path(frame.filename).name+':'+str(frame.lineno),kind=type(error).__name__,code=getattr(error,'code','verification_failed'))
        raise SystemExit(1) from None
