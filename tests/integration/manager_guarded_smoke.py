"""Final systemd/mount-identity smoke on existing disposable recipe projects.

Systemd and the smoke load the final package installed in the venv. The missing-mount case
changes only expected identity in the isolated manager database; no filesystem is
unmounted and existing NAS data/configuration are not rewritten.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from manager_smoke import guard, emit, run, EVENTS

STATE=Path('/root/nas-v030-manager-test')


def main():
    guard()
    from nas_setup.catalog import APPS
    from nas_setup.engine import Engine
    from nas_setup.system import System
    import nas_setup.manager as manager_module
    assert Path(manager_module.__file__).is_relative_to(Path('/opt/nas-setup/venv'))
    engine=Engine(STATE,System(STATE))
    resume=sys.argv[1:]==['--resume-restoration']
    if sys.argv[1:] and not resume:raise ValueError('Unsupported smoke argument')
    if resume:
        log=Path('/root/nas-v030-manager-guarded.log')
        assert log.is_file() and not log.is_symlink() and log.stat().st_uid==0
        prior=[json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
        # The invocation's guard event is already appended to the log. Select
        # the preceding complete lifecycle attempt, requiring all three proofs.
        starts=[i for i,event in enumerate(prior) if event.get('event')=='guards_pass']
        previous=prior[starts[-2]:starts[-1]]
        assert {e.get('app') for e in previous if e.get('event')=='guarded_stop_start_restart_pass'}=={'radarr','sonarr','prowlarr'}
        assert any(e.get('event')=='missing_identity_guard_pass' for e in previous)
        EVENTS[:]=[e for e in previous if e.get('event')!='MANAGER_GUARDED_SMOKE_FAILED']+EVENTS
        emit('guarded_restoration_resumed',completed_lifecycle_and_identity_proofs_preserved=True)
    # Earlier, unpublished smoke snapshot predates durable plan staging. Seed
    # only its known catalog fixtures from immutable private approved files.
    import yaml
    from nas_setup.compose_import import compose_text, port
    def unescape(value):
        if isinstance(value,str):return value.replace('$$','$')
        if isinstance(value,list):return [unescape(item) for item in value]
        if isinstance(value,dict):return {key:unescape(item) for key,item in value.items()}
        return value
    for app in APPS:
        project='nas-manager-'+app['id'];stage=engine.manager.pending/(project+'.json')
        if stage.exists():continue
        record=engine.manager._projects()[project]
        original=STATE/'manager-projects'/project/'compose.yml'
        assert not original.is_symlink() and original.stat().st_uid==0 and original.stat().st_mode&0o077==0
        text=original.read_text();config=unescape(yaml.safe_load(text))
        assert compose_text(config)==text and config['name']==project
        assert set(config['services'])==set(record['services'])=={app['id']}
        for name,service in config['services'].items():
            expected=record['services'][name]
            assert service['image']==expected['image']==app['image']
            assert service.get('volumes',[])==expected['mounts']
            assert [port(value) for value in service.get('ports',[])]==expected['ports']
            assert service['labels']['io.nas-manager.owner']==record['owner']
        staged={'name':project,'config':config,'mounts':record['mounts'],'create':[],'user':None,'record':record}
        engine.manager._write(stage,json.dumps(staged))
        emit('test_fixture_checkpoint_migrated',project=project,original_compose_unchanged=True)
    # Migrate only the exact unpublished VM unit generated before clean Compose
    # interruption exit statuses were declared. Production adoption stays strict.
    import ast
    import inspect
    import textwrap
    tree=ast.parse(textwrap.dedent(inspect.getsource(manager_module.Manager._install_supervision)))
    template=next(node.value.func.value.value for node in ast.walk(tree)
                  if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='unit' for t in node.targets)
                  and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute)
                  and isinstance(node.value.func.value,ast.Constant))
    for record in engine.manager._projects().values():
        unit=Path('/etc/systemd/system')/(record['name']+'.service')
        if not unit.exists():continue
        assert unit.is_file() and not unit.is_symlink() and unit.stat().st_uid==0 and unit.stat().st_mode&0o077==0
        mounts=[run(['systemd-escape','--path','--suffix=mount',root]).strip() for root in sorted(set(record['mounts'].values()))]
        expected=template.format(mounts=' '.join(mounts),directory=manager_module.unit_quote(STATE),name=record['name'],config=manager_module.unit_quote(STATE/'manager-projects'/record['name']/'guarded.yml'))
        if unit.read_text()==expected:continue
        assert expected.count('SuccessExitStatus=130 143\n')==1
        assert unit.read_text()==expected.replace('SuccessExitStatus=130 143\n','')
        import os
        import tempfile
        fd,temporary=tempfile.mkstemp(prefix='.smoke-unit-',dir=unit.parent)
        try:
            with os.fdopen(fd,'w') as stream:
                stream.write(expected);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,unit)
        finally:Path(temporary).unlink(missing_ok=True)
        run(['systemctl','daemon-reload'])
        emit('test_fixture_unit_migrated',project=record['name'],change='SuccessExitStatus=130 143 only')
    def api(op,data=None):return engine.call('manager_'+op,{'session':'guarded-smoke','data':data or {}})
    def wait(job):
        deadline=time.monotonic()+300
        while True:
            job=api('job',{'id':job['id']})
            if job['status']!='running':
                emit('guarded_job_terminal',id=job['id'],operation=job['operation'],status=job['status'],error=job.get('error'))
                assert job['status']=='succeeded'
                return job
            if time.monotonic()>deadline:raise TimeoutError('Guarded action timeout')
            time.sleep(2)
    def identity(project):
        result=run(['docker','ps','-aq','--no-trunc','--filter','label=com.docker.compose.project='+project]).split()
        assert len(result)==1
        return result[0]
    def container_state(container):return run(['docker','inspect','--format','{{.State.Status}}',container]).strip()
    # A tiny local image produces truly absent labels/health metadata without
    # downloading another image. It is never started and has no host mounts.
    import io
    import tarfile
    external='nas-v030-unlabeled-smoke'
    if not run(['docker','ps','-aq','--filter','name=^/'+external+'$']).strip():
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w') as stream:
            item=tarfile.TarInfo('disposable-smoke');item.size=0;stream.addfile(item)
        result=subprocess.run(['docker','import','-','nas-v030-unlabeled:local'],input=archive.getvalue(),capture_output=True,timeout=90)
        assert result.returncode==0
        run(['docker','create','--network','none','--name',external,'nas-v030-unlabeled:local','/unused'])
    overview=api('overview')['containers']
    unlabeled=next(c for c in overview if c['name']==external)
    assert not unlabeled['managed'] and unlabeled['project']=='' and unlabeled['state']=='created'
    assert run(['docker','inspect','--format','{{json .Config.Labels}}',unlabeled['id']]).strip() in ('null','{}')
    assert api('logs',{'id':unlabeled['id']})['text'].strip()==''
    manual=next(c for c in overview if c['name']=='nas-v030-external-smoke')
    assert isinstance(api('logs',{'id':manual['id']})['text'],str)
    emit('optional_docker_metadata_pass',unlabeled_container_visible=True,unlabeled_logs_read=True,external_no_healthcheck_logs_read=True)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def ready(port):
        deadline=time.monotonic()+600
        while True:
            try:
                with opener.open('http://127.0.0.1:'+str(port)+'/',timeout=10) as response:
                    if response.status==200:return
            except (urllib.error.URLError,ConnectionError,TimeoutError):pass
            if time.monotonic()>deadline:raise TimeoutError('Guarded app HTTP timeout')
            time.sleep(3)
    originals={app['id']:hashlib.sha256((STATE/'manager-projects'/('nas-manager-'+app['id'])/'compose.yml').read_bytes()).hexdigest() for app in APPS}
    if not resume:
        for app in APPS:
            project='nas-manager-'+app['id'];unit=project+'.service'
            wait(api('action',{'id':identity(project),'action':'restart'}))
            assert run(['systemctl','is-enabled',unit]).strip()=='enabled'
            assert run(['systemctl','is-active',unit]).strip()=='active'
            assert run(['docker','inspect','--format','{{.HostConfig.RestartPolicy.Name}}',identity(project)]).strip()=='no'
            ready(app['port']);emit('guarded_migration_pass',app=app['id'],unit=unit,docker_restart='no',http_status=200)
            wait(api('action',{'id':identity(project),'action':'stop'}))
            assert run(['systemctl','show',unit,'-p','ActiveState','--value']).strip()=='inactive'
            assert container_state(identity(project))=='exited'
            assert run(['systemctl','show',unit,'-p','UnitFileState','--value']).strip()=='disabled'
            wait(api('action',{'id':identity(project),'action':'start'}))
            assert container_state(identity(project))=='running'
            started=run(['docker','inspect','--format','{{.State.StartedAt}}',identity(project)]).strip()
            wait(api('action',{'id':identity(project),'action':'restart'}))
            restarted=run(['docker','inspect','--format','{{.State.StartedAt}}',identity(project)]).strip()
            assert restarted!=started
            assert run(['systemctl','is-active',unit]).strip()=='active'
            assert run(['systemctl','is-enabled',unit]).strip()=='enabled'
            ready(app['port']);emit('guarded_stop_start_restart_pass',app=app['id'],http_status=200,container_restart_verified=True)
        project='nas-manager-radarr';unit=project+'.service'
        wait(api('action',{'id':identity(project),'action':'stop'}))
        with engine.manager.connect() as db:
            original=db.execute('SELECT body FROM projects WHERE name=?',(project,)).fetchone()[0]
            changed=json.loads(original)
            assert len(changed['mounts'])==1
            changed['mounts']={'00000000-0000-0000-0000-000000000000':next(iter(changed['mounts'].values()))}
            db.execute('UPDATE projects SET body=? WHERE name=?',(json.dumps(changed),project))
        try:
            result=subprocess.run(['systemctl','start',unit],capture_output=True,text=True,timeout=90)
            deadline=time.monotonic()+20
            while True:
                status=run(['systemctl','show',unit,'-p','ActiveState','--value']).strip()
                exit_status=run(['systemctl','show',unit,'-p','ExecMainStatus','--value']).strip()
                if status in ('failed','inactive','activating') and exit_status not in ('','0'):break
                if time.monotonic()>deadline:raise AssertionError('Unit remained active with missing mount identity')
                time.sleep(1)
            assert container_state(identity(project))=='exited'
            emit('missing_identity_guard_pass',simulation='isolated database expected UUID replaced; real NAS stays mounted',container_state='exited')
        finally:
            subprocess.run(['systemctl','stop',unit],capture_output=True,text=True,timeout=90)
            with engine.manager.connect() as db:db.execute('UPDATE projects SET body=? WHERE name=?',(original,project))
    project='nas-manager-radarr';unit=project+'.service'
    # A stopped, garbage-collected unit can return 1 from reset-failed.
    subprocess.run(['systemctl','reset-failed',unit],capture_output=True,text=True,timeout=30)
    wait(api('action',{'id':identity(project),'action':'start'}));ready(7878)
    assert container_state(identity(project))=='running'
    for app in APPS:
        assert hashlib.sha256((STATE/'manager-projects'/('nas-manager-'+app['id'])/'compose.yml').read_bytes()).hexdigest()==originals[app['id']]
    emit('guard_restore_pass',http_status=200,immutable_compose_preserved=True)
    emit('MANAGER_GUARDED_SMOKE_PASS')
    (STATE/'guarded-evidence.json').write_text(json.dumps({'disposable_vm_only':True,'physical_nas_tested':False,'actual_unmount_tested':False,'candidate_systemd_dropins':False,'installed_runtime':True,'events':EVENTS},indent=2)+'\n')


if __name__=='__main__':
    try:main()
    except Exception as error:
        import traceback
        frame=traceback.extract_tb(error.__traceback__)[-1]
        emit('MANAGER_GUARDED_SMOKE_FAILED',location=Path(frame.filename).name+':'+str(frame.lineno),kind=type(error).__name__,code=getattr(error,'code','verification_failed'))
        raise SystemExit(1) from None
