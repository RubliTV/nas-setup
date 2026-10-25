import importlib.util
import time
import pytest
from nas_setup.engine import Engine
from nas_setup.system import DemoSystem
from nas_setup.models import SetupError


def engine(tmp_path):
    assert importlib.util.find_spec('nas_setup.setup_engine'),'setup orchestration missing'
    return Engine(tmp_path/'worker',DemoSystem(tmp_path/'demo',delay=.001))


def selection():
    return {'storage':{'mode':'raid1','disks':['demo-disk-1','demo-disk-2']},
            'users':[{'name':'ana'},{'name':'bruno'}],
            'shares':[{'name':'Files','folder':'files','permissions':{'ana':'rw','bruno':'none'}}]}


def request(e,op,data,session='a'):
    return e.call('setup_'+op,{'session':session,'data':data})


def test_plan_confirmation_cannot_be_replayed_or_change_session(tmp_path):
    e=engine(tmp_path);p=request(e,'plan',selection())
    body={'plan_id':p['plan_id'],'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':{'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}}
    with pytest.raises(SetupError,match='plan_missing'):request(e,'start',body,'wrong')
    j=request(e,'start',body)
    assert j['setup'] is True
    with pytest.raises(SetupError,match='plan_missing'):request(e,'start',body)


def test_no_confirmation_or_changed_disk_cannot_install(tmp_path):
    e=engine(tmp_path);p=request(e,'plan',selection())
    with pytest.raises(SetupError,match='confirmation_required'):request(e,'start',{'plan_id':p['plan_id']})
    e.wizard.provisioner.disks[1]['serial']='changed'
    with pytest.raises(SetupError,match='storage_changed'):
        request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':{'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}})


def test_expired_plan_cannot_install(tmp_path):
    e=engine(tmp_path);p=request(e,'plan',selection());e.wizard.plans[p['plan_id']]['expires']=0
    with pytest.raises(SetupError,match='plan_expired'):request(e,'start',{'plan_id':p['plan_id']})


def test_setup_without_apps_creates_resources_without_password_leaks(tmp_path):
    e=engine(tmp_path);p=request(e,'plan',selection())
    j=request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':{'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}})
    for _ in range(200):
        j=e.call('job')
        if j['status'] not in ('running','waiting-for-sync'):break
        time.sleep(.01)
    assert j['status']=='succeeded',j
    assert j['resources']['shares'][0]['permissions']['bruno']=='none'
    for path in (tmp_path/'worker').rglob('*'):
        if path.is_file() and 'credentials' not in path.name:
            assert b'abcdefghijkl' not in path.read_bytes()
    assert not list((tmp_path/'worker').rglob('credentials.json'))


def test_restart_removes_stale_credentials_and_consent(tmp_path):
    from nas_setup.setup_engine import private_json
    e=engine(tmp_path);request(e,'plan',selection())
    path=e.wizard.directory/'old'/'credentials.json'
    private_json(path,{'ana':'abcdefghijkl'})
    restarted=engine(tmp_path)
    assert restarted.wizard.plans=={} and not path.exists()


def test_setup_api_sanitizes_invalid_password_and_binds_session(tmp_path):
    from fastapi.testclient import TestClient
    from nas_setup.web import create_app
    e=engine(tmp_path);client=TestClient(create_app(e,tmp_path/'web','http://127.0.0.1:8765',demo=True),base_url='http://127.0.0.1:8765')
    auth=client.post('/api/demo-login').json();headers={'X-CSRF-Token':auth['csrf']}
    response=client.post('/api/setup/plan',json=selection(),headers=headers)
    assert response.status_code==200,response.text
    plan=response.json()
    bad=client.post('/api/setup/start',json={'plan_id':plan['plan_id'],'passwords':{'ana':'SECRET'}},headers=headers)
    assert bad.status_code==422 and 'SECRET' not in bad.text
    denied=client.post('/api/setup/start',json={'plan_id':plan['plan_id']})
    assert denied.status_code==403


def test_recovery_plan_can_review_own_mounted_disks_and_is_resume_only(tmp_path,monkeypatch):
    e=engine(tmp_path);monkeypatch.setattr(e.wizard,'_launch',lambda job:None)
    p=request(e,'plan',selection())
    credentials={'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}
    consent={'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':credentials}
    job=request(e,'start',{'plan_id':p['plan_id'],**consent})
    e.store.step(job['id'],'storage_prerequisites','succeeded')
    e.store.update(job['id'],status='interrupted')
    e.wizard.grants.clear()
    e.wizard.provisioner.disks[1].update(eligible=False,reason='mounted')
    calls=[]
    monkeypatch.setattr(e.wizard.provisioner,'review_recovery',lambda plan,journal:calls.append(plan),raising=False)
    p=request(e,'plan',selection(),'new-session')
    assert p['recovery'] is True and calls
    with pytest.raises(SetupError,match='recovery_resume_only'):
        request(e,'start',{'plan_id':p['plan_id'],**consent},'new-session')
    assert request(e,'resume',{'plan_id':p['plan_id'],**consent},'new-session')['id']==job['id']


def test_changed_recovery_state_invalidates_new_consent(tmp_path,monkeypatch):
    from nas_setup.journal import OperationJournal
    e=engine(tmp_path);monkeypatch.setattr(e.wizard,'_launch',lambda job:None)
    p=request(e,'plan',selection());credentials={'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}
    consent={'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':credentials}
    job=request(e,'start',{'plan_id':p['plan_id'],**consent})
    e.store.update(job['id'],status='interrupted');e.wizard.grants.clear()
    monkeypatch.setattr(e.wizard.provisioner,'review_recovery',lambda plan,journal:None,raising=False)
    p=request(e,'plan',selection())
    OperationJournal(e.wizard.directory/job['id']/'journal').perform('new-result',lambda:{'identity':'changed'})
    with pytest.raises(SetupError,match='storage_changed'):
        request(e,'resume',{'plan_id':p['plan_id'],**consent})


def test_authenticated_session_can_resume_exact_saved_paused_job(tmp_path,monkeypatch):
    e=engine(tmp_path);monkeypatch.setattr(e.wizard,'_launch',lambda job:None)
    p=request(e,'plan',selection());credentials={'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}
    job=request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':credentials})
    e.store.update(job['id'],status='paused')
    resumed=request(e,'resume',{'passwords':credentials},'new-session')
    assert resumed['id']==job['id'] and resumed['selection']==job['selection']


@pytest.mark.parametrize('operation',['job','finish'])
def test_completed_storage_is_rechecked_before_finish(tmp_path,monkeypatch,operation):
    e=engine(tmp_path);p=request(e,'plan',selection())
    job=request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':{'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}})
    for _ in range(200):
        job=e.call('job')
        if job['status']=='succeeded':break
        time.sleep(.01)
    assert job['status']=='succeeded'
    monkeypatch.setattr(e.wizard.provisioner,'status',lambda resources:{'state':'degraded','progress':0,'tolerance':1})
    if operation=='finish':
        with pytest.raises(SetupError,match='job_active'):e.call('finish')
    else:
        assert e.call('job')['error']=='raid_degraded'
    assert e.store.get()['status']=='failed'
    # Resume cannot skip a formerly successful RAID check.
    request(e,'resume',{})
    for _ in range(200):
        job=e.call('job')
        if job['status']!='running':break
        time.sleep(.01)
    assert job['status']=='failed' and job['error']=='raid_degraded'


def test_recovery_after_completed_format_requires_no_new_erasure(tmp_path,monkeypatch):
    from nas_setup.journal import OperationJournal
    e=engine(tmp_path);monkeypatch.setattr(e.wizard,'_launch',lambda job:None)
    p=request(e,'plan',selection());credentials={'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}
    job=request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':credentials})
    e.store.update(job['id'],status='interrupted');e.wizard.grants.clear()
    OperationJournal(e.wizard.directory/job['id']/'journal').perform('filesystem.result',lambda:{'uuid':'own-filesystem'})
    monkeypatch.setattr(e.wizard.provisioner,'review_recovery',lambda plan,journal:None)
    p=request(e,'plan',selection())
    assert p['recovery'] and not p['destructive'] and not p['confirmation_required']
    assert 'erase_selected_disks' not in p['warnings']
    assert request(e,'resume',{'plan_id':p['plan_id'],'passwords':credentials})['id']==job['id']


@pytest.mark.parametrize('value',['a'*12+'\n','a'*12+'\r','a'*12+'\0'])
def test_invalid_account_password_rejected_before_storage(value):
    from nas_setup.setup_engine import SetupStart
    with pytest.raises(ValueError):SetupStart(passwords={'ana':value})


def test_failed_job_resumes_original_authorized_plan_after_restart(tmp_path,monkeypatch):
    e=engine(tmp_path);monkeypatch.setattr(e.wizard,'_launch',lambda job:None)
    p=request(e,'plan',selection());credentials={'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}
    job=request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':credentials})
    e.store.step(job['id'],'storage_prerequisites','succeeded')
    e.store.step(job['id'],'storage_setup','failed')
    e.store.update(job['id'],status='failed',error='omv_configuration_changed')
    restarted=engine(tmp_path);monkeypatch.setattr(restarted.wizard,'_launch',lambda job:None)
    result=request(restarted,'resume',{'passwords':credentials},'new-authenticated-session')
    assert result['id']==job['id'] and result['status']=='running'
    assert result['selection']==job['selection']


def test_retry_checks_recovery_even_with_same_session_grant(tmp_path,monkeypatch):
    e=engine(tmp_path);monkeypatch.setattr(e.wizard,'_launch',lambda job:None)
    p=request(e,'plan',selection());credentials={'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}
    job=request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':credentials})
    e.store.update(job['id'],status='failed')
    def uncertain(plan,journal):raise SetupError('recovery_required')
    monkeypatch.setattr(e.wizard.provisioner,'review_recovery',uncertain)
    with pytest.raises(SetupError,match='recovery_required'):
        request(e,'resume',{'passwords':credentials})
    assert e.store.get()['status']=='failed'


def test_pending_configuration_requires_job_and_session_bound_approval(tmp_path,monkeypatch):
    e=engine(tmp_path);monkeypatch.setattr(e.wizard,'_launch',lambda job:None)
    credentials={'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}
    p=request(e,'plan',selection())
    job=request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE','erase_disks':['demo-disk-1','demo-disk-2'],'passwords':credentials})
    e.store.step(job['id'],'storage_prerequisites','succeeded')
    e.store.update(job['id'],status='failed',error='omv_configuration_changed')
    pending={'digest':'approved-config','modules':['fstab','mdadm']}
    monkeypatch.setattr(e.wizard.provisioner,'pending_configuration',lambda plan,journal:dict(pending),raising=False)
    offer=request(e,'recovery',{})
    assert offer['modules']==['fstab','mdadm'] and 'digest' not in offer
    with pytest.raises(SetupError,match='confirmation_required'):
        request(e,'resume',{'pending_id':offer['pending_id'],'passwords':credentials},'other-session')
    e.wizard.pending_offers[offer['pending_id']]['expires']=0
    with pytest.raises(SetupError,match='omv_configuration_changed'):
        request(e,'resume',{'pending_id':offer['pending_id'],'passwords':credentials})
    e.wizard.pending_offers[offer['pending_id']]['expires']=time.time()+900
    pending['digest']='external-change'
    with pytest.raises(SetupError,match='omv_configuration_changed'):
        request(e,'resume',{'pending_id':offer['pending_id'],'passwords':credentials})
    assert e.store.get()['status']=='failed'
    pending['digest']='approved-config'
    resumed=request(e,'resume',{'pending_id':offer['pending_id'],'passwords':credentials})
    assert resumed['id']==job['id'] and resumed['status']=='running'
    calls=[]
    monkeypatch.setattr(e.wizard.provisioner,'apply_pending_configuration',lambda plan,journal,snapshot:calls.append(snapshot),raising=False)
    e.wizard._run(resumed)
    assert calls==[{'digest':'approved-config','modules':['fstab','mdadm']}]
    assert not e.wizard.pending_runs


def test_pending_configuration_offer_requires_csrf_and_authenticated_session(tmp_path):
    from fastapi.testclient import TestClient
    from nas_setup.web import create_app
    e=engine(tmp_path);client=TestClient(create_app(e,tmp_path/'web','http://127.0.0.1:8765',demo=True),base_url='http://127.0.0.1:8765')
    assert client.post('/api/setup/recovery',json={}).status_code==401
    auth=client.post('/api/demo-login').json()
    assert client.post('/api/setup/recovery',json={}).status_code==403
    assert client.post('/api/setup/recovery',json={},headers={'X-CSRF-Token':auth['csrf']}).json()['error']=='job_missing'


def test_resume_only_requests_passwords_for_unfinished_accounts(tmp_path, monkeypatch):
    from nas_setup.journal import OperationJournal
    e=engine(tmp_path);monkeypatch.setattr(e.wizard,'_launch',lambda job:None)
    p=request(e,'plan',selection())
    job=request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE',
        'erase_disks':['demo-disk-1','demo-disk-2'],'passwords':{'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}})
    e.store.update(job['id'],status='failed')
    journal=OperationJournal(e.wizard.directory/job['id']/'journal')
    journal.perform('user.ana',lambda:{'name':'ana','uid':1001})
    assert e.call('job')['resume_password_users']==['bruno']
    resumed=request(e,'resume',{'passwords':{'bruno':'mnopqrstuvwx'}})
    assert resumed['status']=='running'
    assert e.call('job')['resume_password_users']==[]
    e.store.update(job['id'],status='failed')
    journal.perform('user.bruno',lambda:{'name':'bruno','uid':1002})
    assert e.call('job')['resume_password_users']==[]
    assert request(e,'resume',{})['status']=='running'


def test_uncertain_account_creation_still_requires_credentials(tmp_path,monkeypatch):
    from nas_setup.journal import OperationJournal
    e=engine(tmp_path);monkeypatch.setattr(e.wizard,'_launch',lambda job:None)
    p=request(e,'plan',selection())
    job=request(e,'start',{'plan_id':p['plan_id'],'confirmation':'ERASE',
        'erase_disks':['demo-disk-1','demo-disk-2'],'passwords':{'ana':'abcdefghijkl','bruno':'mnopqrstuvwx'}})
    journal=OperationJournal(e.wizard.directory/job['id']/'journal')
    def interrupted(): raise RuntimeError('lost response')
    with pytest.raises(RuntimeError): journal.perform('user.ana',interrupted)
    e.store.update(job['id'],status='failed')
    assert e.call('job')['resume_password_users']==['ana','bruno']
    with pytest.raises(SetupError,match='password_required'):request(e,'resume',{})
