import json
import threading
import time
from types import SimpleNamespace
import pytest
from nas_setup.engine import Engine
from nas_setup.manager import Manager
from nas_setup.models import SetupError
from nas_setup.system import DemoSystem


def manager(tmp_path):
    engine=Engine(tmp_path/'worker',DemoSystem(tmp_path/'demo',delay=0))
    return Manager(engine)


def call(m,op,data=None,session='session-one'):
    return m.call(op,{'session':session,'data':data or {}})


def wait(m,job):
    for _ in range(200):
        job=call(m,'job',{'id':job['id']})
        if job['status']!='running':return job
        time.sleep(.01)
    raise AssertionError('job did not finish')


def test_plan_session_scoped_immutable_and_one_use(tmp_path):
    m=manager(tmp_path)
    plan=call(m,'plan',{'kind':'compose','name':'hello','compose':'services: {web: {image: "nginx:1.28"}}'})
    with pytest.raises(SetupError,match='plan_expired'):call(m,'deploy',{'plan_id':plan['plan_id']},session='other')
    with pytest.raises(SetupError):call(m,'deploy',{'plan_id':plan['plan_id'],'compose':'evil'})
    job=call(m,'deploy',{'plan_id':plan['plan_id']})
    assert wait(m,job)['status']=='succeeded'
    with pytest.raises(SetupError,match='plan_expired'):call(m,'deploy',{'plan_id':plan['plan_id']})
    assert call(m,'overview')['containers'][0]['project']=='nas-manager-hello'
    assert Manager(m.engine).call('job',{'session':'x','data':{}})['status']=='succeeded'


def test_catalog_plan_existing_account_media_and_deploy(tmp_path):
    m=manager(tmp_path);options=call(m,'options')
    media=options['shares'][0]['path']
    plan=call(m,'plan',{'kind':'catalog','app':'radarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name'],'media':media,'port':7878})
    job=wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))
    assert job['status']=='succeeded'
    container=call(m,'overview')['containers'][0]
    assert container['managed']
    assert wait(m,call(m,'action',{'id':container['id'],'action':'stop'}))['status']=='succeeded'
    assert call(m,'overview')['containers'][0]['state']=='exited'


def test_setup_active_blocks_manager_without_consuming_plan(tmp_path):
    m=manager(tmp_path)
    plan=call(m,'plan',{'kind':'compose','name':'hello','compose':'services: {web: {image: "nginx:1.28"}}'})
    m.engine.store.create({},['preflight'])
    with pytest.raises(SetupError,match='job_active'):call(m,'deploy',{'plan_id':plan['plan_id']})


def test_missing_docker_explained_and_external_update_rejected(tmp_path):
    m=manager(tmp_path);m.system.demo=False
    m.system.docker_available=lambda:False
    assert call(m,'overview')['docker']=='unavailable'
    with pytest.raises(SetupError,match='docker_unavailable'):call(m,'action',{'id':'a'*64,'action':'stop'})


def test_no_secret_in_preview_job_or_logs(tmp_path):
    m=manager(tmp_path)
    plan=call(m,'plan',{'kind':'compose','name':'hello','compose':'services: {web: {image: "nginx:1.28", environment: {PASSWORD: "hidden-value"}}}'})
    job=wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))
    assert 'hidden-value' not in json.dumps([plan,job,call(m,'overview')])
    assert 'hidden-value' not in m.db.read_bytes().decode(errors='ignore')


def test_restart_marks_running_jobs_interrupted(tmp_path):
    m=manager(tmp_path)
    with m.connect() as c:
        c.execute('INSERT INTO jobs VALUES(?,?,?)',('abc',json.dumps({'id':'abc','status':'running','steps':[]}),1))
    m=Manager(m.engine)
    assert call(m,'job')['status']=='interrupted'


class Host:
    demo=False
    def __init__(self,directory):
        self.directory=directory;self.commands=[];self.available=True
        self.info={'id':'a'*64,'name':'/external','image':'nginx:1.28','state':'running','project':'other',
                   'service':'web','owner':None,'ports':{'80/tcp':[{'HostIp':'0.0.0.0','HostPort':'8080'}]},'mounts':[]}
    def inventory(self):return {'volumes':[]}
    def docker_available(self):return self.available
    def run(self,args,timeout=60,check=True):
        self.commands.append(args)
        if args[:2]==['docker','info']:out='29.0'
        elif args[:2]==['docker','ps']:out=self.info['id']
        elif args[:2]==['docker','stats']:out=json.dumps({'ID':'a'*12,'CPUPerc':'2.0%','MemUsage':'32 MiB / 1 GiB'})
        elif args[:2]==['docker','inspect']:out=json.dumps({'env':['PASSWORD=super-private'],'cmd':None,'entrypoint':None,'healthcheck':None}) if '"env"' in args[3] else json.dumps(self.info)
        elif args[:2]==['docker','logs']:out='ready\nsuper-private\npassword generated: surprise\n'
        elif args[:2] in (['docker','stop'],['systemctl','stop']):self.info['state']='exited';out=''
        else:raise AssertionError(args)
        return SimpleNamespace(stdout=out,stderr='',returncode=0)


def host_manager(tmp_path):
    from nas_setup.jobs import JobStore
    host=Host(tmp_path)
    engine=SimpleNamespace(system=host,store=JobStore(tmp_path/'worker'),lock=threading.Lock())
    return Manager(engine)


def test_real_boundary_external_action_preserves_configuration_and_stats(tmp_path):
    m=host_manager(tmp_path)
    container=call(m,'overview')['containers'][0]
    assert not container['managed'] and container['cpu']=='2.0%'
    assert container['ports']==[{'host':8080,'container':80,'protocol':'tcp'}]
    with pytest.raises(SetupError,match='external_update_unsupported'):call(m,'action',{'id':'a'*64,'action':'update'})
    assert wait(m,call(m,'action',{'id':'a'*64,'action':'stop'}))['status']=='succeeded'
    assert call(m,'overview')['containers'][0]['state']=='exited'
    assert ['docker','stop','a'*64] in m.system.commands
    assert not any('rm' in c or 'compose' in c for c in m.system.commands)


def test_legacy_owned_stop_uses_systemd_and_logs_hide_secrets(tmp_path):
    m=host_manager(tmp_path);m.system.info['project']='nas-setup-jellyfin'
    m.system.check_project_ownership=lambda app:True
    m.system.guard_stack=lambda app:{}
    assert wait(m,call(m,'action',{'id':'a'*64,'action':'stop'}))['status']=='succeeded'
    assert ['systemctl','stop','nas-app-jellyfin.service'] in m.system.commands
    text=call(m,'logs',{'id':'a'*64})['text']
    assert 'super-private' not in text and 'surprise' not in text and 'ready' in text


def test_plan_deploy_rejects_changed_mount_before_write(tmp_path):
    m=manager(tmp_path);options=call(m,'options')
    media=options['shares'][0]['path']
    plan=call(m,'plan',{'kind':'compose','name':'hello','compose':f'services: {{web: {{image: "nginx:1.28", restart: unless-stopped, volumes: ["{media}:/data"]}}}}'})
    from pathlib import Path
    Path(media).rmdir();Path(media).symlink_to('/etc')
    with pytest.raises(SetupError,match='unsafe_storage'):call(m,'deploy',{'plan_id':plan['plan_id']})
    assert not list(m.projects.iterdir())


def test_prowlarr_plan_does_not_require_media_share(tmp_path):
    m=manager(tmp_path);options=call(m,'options')
    plan=call(m,'plan',{'kind':'catalog','app':'prowlarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name'],'media':''})
    assert len(plan['summary']['services'][0]['mounts'])==1
    assert wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))['status']=='succeeded'


def test_retry_failed_deploy_preserves_private_config_and_data(tmp_path):
    m=manager(tmp_path)
    plan=call(m,'plan',{'kind':'compose','name':'hello','compose':'services: {web: {image: "nginx:1.28", environment: {PASSWORD: "hidden-value"}}}'})
    job=wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))
    m._change(job['id'],status='failed',error='manager_command_failed')
    config=m.projects/'nas-manager-hello'/'compose.yml';original=config.read_bytes()
    retried=wait(m,call(m,'retry',{'id':job['id']}))
    assert retried['status']=='succeeded'
    assert config.read_bytes()==original
    assert 'hidden-value' not in json.dumps(retried)
    with pytest.raises(SetupError):call(m,'retry',{'id':retried['id']})


def test_overview_paginates_all_containers_with_bounded_response(tmp_path):
    m=manager(tmp_path)
    for n in range(130):m._save_project({'name':f'nas-manager-p{n}','services':{'web':{'image':'nginx:1.28','ports':[]}}})
    offset=0;seen=[]
    while offset is not None:
        page=call(m,'overview',{'offset':offset});seen.extend(c['id'] for c in page['containers'])
        assert len(json.dumps(page).encode())<262144
        offset=page['next_offset']
    assert len(set(seen))==131 and page['total']==131


def test_demo_external_container_can_stop_and_restart_but_not_update(tmp_path):
    m=manager(tmp_path)
    external=next(c for c in call(m,'overview')['containers'] if not c['managed'])
    assert 'update' not in external['actions']
    assert wait(m,call(m,'action',{'id':external['id'],'action':'stop'}))['status']=='succeeded'
    restored=Manager(m.engine)
    assert next(c for c in call(restored,'overview')['containers'] if c['id']==external['id'])['state']=='exited'
    assert wait(restored,call(restored,'action',{'id':external['id'],'action':'start'}))['status']=='succeeded'
    with pytest.raises(SetupError,match='external_update_unsupported'):call(restored,'action',{'id':external['id'],'action':'update'})
    assert 'Demo' in call(restored,'logs',{'id':external['id']})['text']


def test_logs_redact_before_truncating_and_cover_command_secrets(tmp_path):
    m=host_manager(tmp_path);original=m.system.run
    def command(args,timeout=60,check=True):
        if args[:2]==['docker','inspect'] and '"env"' in args[3]:
            if args[3]=='{{json .Config.Env}}':out=json.dumps(['PASSWORD=super-private'])
            else:out=json.dumps({'env':['PASSWORD=super-private'],'cmd':['sh','-c','echo command-private'],'entrypoint':['app','--secret=entry-private'],'healthcheck':{'Test':['CMD','check','--token=health-private']}})
            return SimpleNamespace(stdout=out,stderr='',returncode=0)
        if args[:2]==['docker','logs']:
            return SimpleNamespace(stdout='super-private'+('x'*32760)+'\ncommand-private\nentry-private\nhealth-private',stderr='',returncode=0)
        return original(args,timeout,check)
    m.system.run=command
    text=call(m,'logs',{'id':'a'*64})['text']
    assert 'private' not in text
    assert len(text)<=32768


def test_secret_spanning_log_truncation_boundary_does_not_leak_suffix(tmp_path):
    m=host_manager(tmp_path);original=m.system.run
    def command(args,timeout=60,check=True):
        if args[:2]==['docker','logs']:return SimpleNamespace(stdout='super-private'+('x'*32760),stderr='',returncode=0)
        return original(args,timeout,check)
    m.system.run=command
    assert 'private' not in call(m,'logs',{'id':'a'*64})['text']


def test_real_pull_failure_can_retry_saved_configuration(tmp_path):
    m=manager(tmp_path)
    plan=call(m,'plan',{'kind':'compose','name':'hello','compose':'services: {web: {image: "nginx:1.28", environment: {PASSWORD: "hidden-value"}}}'})
    m.system.demo=False;m.system.docker_available=lambda:True
    commands=[];failed=False
    def command(args,timeout=60,check=True):
        nonlocal failed
        commands.append(args)
        if args[:2]==['docker','info']:return SimpleNamespace(stdout='29.0',stderr='',returncode=0)
        if args[-1]=='pull' and not failed:
            failed=True;return SimpleNamespace(stdout='',stderr='hidden-value',returncode=1)
        return SimpleNamespace(stdout='',stderr='',returncode=0)
    m.system.run=command
    job=wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))
    assert job['status']=='failed' and job['error']=='manager_command_failed'
    assert 'hidden-value' not in json.dumps(job)
    retry=wait(m,call(m,'retry',{'id':job['id']}))
    assert retry['status']=='succeeded'
    assert sum(c[-1]=='pull' for c in commands)==2
    assert any('up' in c and '--no-build' in c for c in commands)
    assert not any('down' in c or '--volumes' in c for c in commands)


def test_failed_private_inspection_never_returns_logs(tmp_path):
    m=host_manager(tmp_path);original=m.system.run
    def command(args,timeout=60,check=True):
        if args[:2]==['docker','inspect'] and '"env"' in args[3]:return SimpleNamespace(stdout='',stderr='secret',returncode=1)
        return original(args,timeout,check)
    m.system.run=command
    with pytest.raises(SetupError):call(m,'logs',{'id':'a'*64})
    assert not any(args[:2]==['docker','logs'] for args in m.system.commands)


def test_failed_compose_write_resumes_validation_after_restart(tmp_path):
    m=manager(tmp_path);options=call(m,'options')
    plan=call(m,'plan',{'kind':'catalog','app':'prowlarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name']})
    original=m._write
    def fail_compose(path,text):
        if path.name=='compose.yml':raise OSError('disk full')
        original(path,text)
    m._write=fail_compose
    job=wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))
    assert job['status']=='failed'
    recovered=Manager(m.engine)
    assert wait(recovered,call(recovered,'retry',{'id':job['id']}))['status']=='succeeded'
    assert (recovered.projects/'nas-manager-prowlarr'/'compose.yml').is_file()


def test_staging_failure_has_no_app_mutations_and_plan_can_be_retried(tmp_path):
    m=manager(tmp_path);options=call(m,'options')
    plan=call(m,'plan',{'kind':'catalog','app':'prowlarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name']})
    original=m._write
    m._write=lambda *args:(_ for _ in ()).throw(OSError('disk full'))
    with pytest.raises(SetupError):call(m,'deploy',{'plan_id':plan['plan_id']})
    from pathlib import Path
    assert not (Path(options['volumes'][0]['path'])/'.nas-manager').exists()
    m._write=original
    assert wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))['status']=='succeeded'


def test_staged_plan_without_job_is_recovered_as_interrupted_without_replay(tmp_path):
    m=manager(tmp_path)
    plan=call(m,'plan',{'kind':'compose','name':'hello','compose':'services: {web: {image: "nginx:1.28"}}'})
    m._new_job=lambda *args:(_ for _ in ()).throw(OSError('database disk full'))
    with pytest.raises(SetupError):call(m,'deploy',{'plan_id':plan['plan_id']})
    assert not list(m.projects.iterdir())
    recovered=Manager(m.engine);job=call(recovered,'job')
    assert job['status']=='interrupted' and not list(m.projects.iterdir())
    assert wait(recovered,call(recovered,'retry',{'id':job['id']}))['status']=='succeeded'


def test_interrupted_validate_reuses_owned_partial_directories(tmp_path):
    m=manager(tmp_path);options=call(m,'options')
    plan=call(m,'plan',{'kind':'catalog','app':'prowlarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name']})
    original=m._prepare_storage
    def interrupt_after_prepare(plan):
        original(plan);raise OSError('simulated interruption after mkdir')
    m._prepare_storage=interrupt_after_prepare
    job=wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))
    m._change(job['id'],status='running')
    recovered=Manager(m.engine)
    assert call(recovered,'job')['status']=='interrupted'
    assert wait(recovered,call(recovered,'retry',{'id':job['id']}))['status']=='succeeded'


def test_prepare_docker_is_durable_and_serialized(tmp_path):
    m=manager(tmp_path)
    job=wait(m,call(m,'prepare_docker'))
    assert job['status']=='succeeded' and job['operation']=='prepare_docker'
    assert 'docker' in m.system.installed
    m.engine.store.create({},['preflight'])
    with pytest.raises(SetupError,match='job_active'):call(m,'prepare_docker')


def test_prepare_docker_rejects_unsupported_host(tmp_path):
    m=manager(tmp_path);m.system.inventory=lambda:{'supported':False,'volumes':[]}
    with pytest.raises(SetupError,match='unsupported_host'):call(m,'prepare_docker')


def test_media_permissions_use_container_uid_gid_without_host_extra_groups(tmp_path):
    m=manager(tmp_path);m.system.demo=False;commands=[]
    def command(args,**kwargs):
        commands.append(args);return SimpleNamespace(stdout='',stderr='',returncode=0)
    m.system.run=command
    m._media_permission(tmp_path,{'name':'alice','uid':1001,'gid':100})
    assert all(args[:7]==['setpriv','--reuid','1001','--regid','100','--clear-groups','--'] for args in commands)
    assert {args[-2] for args in commands}=={'-r','-w','-x'}


def supervised_host(m,monkeypatch,tmp_path):
    import nas_setup.manager as module
    monkeypatch.setattr(module,'UNIT_DIRECTORY',tmp_path/'units',raising=False)
    (tmp_path/'units').mkdir(exist_ok=True)
    m.system.demo=False;m.system.docker_available=lambda:True;m.system.check_port=lambda port:None
    commands=[];state={'running':False}
    def command(args,timeout=60,check=True):
        commands.append(args);out=''
        if args[:2]==['docker','info']:out='29.0'
        elif args[0]=='systemd-escape':out='srv-data.mount'
        elif args[:2]==['docker','ps']:out='a'*64 if state['running'] else ''
        elif args[:2]==['docker','inspect']:
            r=next(iter(m._projects().values()));name,s=next(iter(r['services'].items()))
            out=json.dumps({'id':'a'*64,'name':name,'image':s['image'],'state':'running' if state['running'] else 'exited','health':'healthy','exit_code':0,
                'project':r['name'],'service':name,'owner':r['owner'],'ports':{},'mounts':[{'Type':'bind','Source':v['source'],'Destination':v['target'],'RW':not v['read_only']} for v in s['mounts']]})
        elif args[:2]==['docker','compose'] and 'ps' in args:out='a'*64
        elif args[:2] in (['systemctl','start'],['systemctl','restart']) or args[:2]==['systemctl','enable'] and '--now' in args:state['running']=True
        elif args[:2]==['systemctl','stop']:state['running']=False
        return SimpleNamespace(stdout=out,stderr='',returncode=0)
    m.system.run=command
    return commands,state


def test_bound_deployment_uses_mount_supervision_and_no_docker_autostart(tmp_path,monkeypatch):
    import yaml
    m=manager(tmp_path);options=call(m,'options')
    plan=call(m,'plan',{'kind':'catalog','app':'prowlarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name']})
    commands,state=supervised_host(m,monkeypatch,tmp_path)
    job=wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))
    assert job['status']=='succeeded'
    guarded=m.projects/'nas-manager-prowlarr'/'guarded.yml'
    assert yaml.safe_load(guarded.read_text())['services']['prowlarr']['restart']=='no'
    unit=(tmp_path/'units'/'nas-manager-prowlarr.service').read_text()
    assert 'BindsTo=srv-data.mount' in unit and 'After=docker.service network-online.target srv-data.mount' in unit
    assert '--stack-run' in unit and str(m.directory) in unit
    assert 'StandardOutput=null' in unit and 'StandardError=null' in unit
    assert 'SuccessExitStatus=130 143' in unit
    assert state['running']
    assert any(c[:2]==['systemctl','enable'] for c in commands)
    assert wait(m,call(m,'action',{'id':'a'*64,'action':'stop'}))['status']=='succeeded'
    assert not state['running']
    assert ['systemctl','stop','nas-manager-prowlarr.service'] in commands
    assert ['systemctl','disable','nas-manager-prowlarr.service'] in commands
    assert ['docker','stop','a'*64] not in commands


@pytest.mark.parametrize('change',['missing','replaced'])
def test_guarded_runner_refuses_missing_or_replaced_volume_without_exec(tmp_path,monkeypatch,change):
    import nas_setup.manager as module
    m=manager(tmp_path);options=call(m,'options')
    plan=call(m,'plan',{'kind':'catalog','app':'prowlarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name']})
    supervised_host(m,monkeypatch,tmp_path)
    assert wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))['status']=='succeeded'
    inventory=m.system.inventory()
    if change=='missing':inventory['volumes']=[]
    else:inventory['volumes'][0]['id']='different-uuid'
    m.system.inventory=lambda:inventory
    executed=[];monkeypatch.setattr(module.os,'execv',lambda *args:executed.append(args))
    with pytest.raises(SetupError,match='volume_missing'):
        module.run_stack(m.directory,'nas-manager-prowlarr',system=m.system)
    assert executed==[]


def test_guarded_runner_executes_only_after_guards_without_interrupting_jobs(tmp_path,monkeypatch):
    import nas_setup.manager as module
    m=manager(tmp_path);options=call(m,'options')
    plan=call(m,'plan',{'kind':'catalog','app':'prowlarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name']})
    supervised_host(m,monkeypatch,tmp_path)
    job=wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))
    assert job['status']=='succeeded'
    m._change(job['id'],status='running')
    executed=[];monkeypatch.setattr(module.os,'execv',lambda *args:executed.append(args))
    module.run_stack(m.directory,'nas-manager-prowlarr',system=m.system)
    assert call(m,'job')['status']=='running'
    assert executed[0][0]=='/usr/bin/docker'
    assert '--no-recreate' in executed[0][1] and '--no-build' in executed[0][1]


@pytest.mark.parametrize('restart',['no','on-failure',None])
def test_bound_import_rejects_unsupported_restart_before_writes(tmp_path,restart):
    import yaml
    m=manager(tmp_path);media=call(m,'options')['shares'][0]['path']
    service={'image':'nginx:1.28','volumes':[media+':/data']}
    if restart is not None:service['restart']=restart
    with pytest.raises(SetupError,match='compose_lifecycle_unsupported'):
        call(m,'plan',{'kind':'compose','name':'unsupported','compose':yaml.safe_dump({'services':{'web':service}})})
    assert not list(m.projects.iterdir()) and not list(m.pending.iterdir())


def test_bound_import_rejects_one_shot_mixed_dependency_forms(tmp_path):
    import yaml
    m=manager(tmp_path);media=call(m,'options')['shares'][0]['path']
    config={'services':{'web':{'image':'nginx:1.28','restart':'unless-stopped','volumes':[media+':/data'],'depends_on':{'init':{'condition':'service_completed_successfully'}}},
        'init':{'image':'busybox:1.37','restart':'unless-stopped','command':['true']},
        'side':{'image':'nginx:1.28','restart':'unless-stopped','depends_on':['web']}}}
    with pytest.raises(SetupError,match='compose_lifecycle_unsupported'):call(m,'plan',{'kind':'compose','name':'unsupported','compose':yaml.safe_dump(config)})
    assert not list(m.pending.iterdir())


def test_action_scope_distinguishes_managed_app_and_external_container(tmp_path):
    m=manager(tmp_path);options=call(m,'options')
    plan=call(m,'plan',{'kind':'catalog','app':'prowlarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name']})
    assert wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))['status']=='succeeded'
    rows=call(m,'overview')['containers']
    assert next(c for c in rows if c['managed'])['action_scope']=='app'
    assert next(c for c in rows if not c['managed'])['action_scope']=='container'


def test_guarded_restart_stops_containers_even_during_first_unit_adoption(tmp_path,monkeypatch):
    m=manager(tmp_path);options=call(m,'options')
    plan=call(m,'plan',{'kind':'catalog','app':'prowlarr','volume':options['volumes'][0]['id'],'user':options['users'][0]['name']})
    assert wait(m,call(m,'deploy',{'plan_id':plan['plan_id']}))['status']=='succeeded'
    commands,state=supervised_host(m,monkeypatch,tmp_path);state['running']=True
    assert wait(m,call(m,'action',{'id':'a'*64,'action':'restart'}))['status']=='succeeded'
    stops=[i for i,c in enumerate(commands) if c[:2]==['docker','compose'] and c[-1]=='stop']
    restarts=[i for i,c in enumerate(commands) if c[:2]==['systemctl','restart']]
    assert stops and restarts and stops[0]<restarts[0]


def test_docker_strict_optional_health_lookup_does_not_hide_container(tmp_path):
    m=host_manager(tmp_path);original=m.system.run
    def docker29(args,timeout=60,check=True):
        if args[:2]==['docker','inspect'] and '.State.Health' in args[3]:
            # Recorded Docker 29.9.2 response for a container without a
            # healthcheck. The real CLI uses Go's missingkey=error behavior.
            return SimpleNamespace(stdout='',stderr='template parsing error: map has no entry for key "Health"',returncode=1)
        return original(args,timeout,check)
    m.system.run=docker29
    rows=call(m,'overview')['containers']
    assert len(rows)==1 and rows[0]['name']=='external'
    assert wait(m,call(m,'action',{'id':rows[0]['id'],'action':'stop'}))['status']=='succeeded'


def test_unlabelled_container_and_optional_private_config_fields_are_supported(tmp_path):
    m=host_manager(tmp_path);original=m.system.run
    m.system.info.update(project=None,service=None,owner=None)
    def strict_optional(args,timeout=60,check=True):
        if args[:2]==['docker','inspect'] and any(field in args[3] for field in ('.Config.Labels','.Config.Healthcheck','.Config.Env','.Config.Cmd','.Config.Entrypoint')):
            return SimpleNamespace(stdout='',stderr='template parsing error: map has no entry for optional key',returncode=1)
        return original(args,timeout,check)
    m.system.run=strict_optional
    rows=call(m,'overview')['containers']
    assert len(rows)==1 and not rows[0]['managed']
    assert 'ready' in call(m,'logs',{'id':rows[0]['id']})['text']
