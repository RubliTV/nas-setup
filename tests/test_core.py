import importlib.util
import threading
from pathlib import Path
import pytest


def modules():
    assert importlib.util.find_spec('nas_setup.models'), 'Core contracts are not implemented'
    from nas_setup.models import Selection, make_plan, SetupError
    from nas_setup.jobs import JobStore
    from nas_setup.storage import volume_path
    return Selection, make_plan, SetupError, JobStore, volume_path


def inventory(tmp_path):
    d=tmp_path/'disk'; d.mkdir(exist_ok=True)
    return {'supported':True,'memory_gb':8,'cpu_flags':[], 'volumes':[{'id':'disk-a','path':str(d),'free_gb':100,'label':'Dados','fstype':'ext4'}]}


def test_plan_needs_existing_storage_for_apps(tmp_path):
    Selection,plan,Error,*_=modules()
    with pytest.raises(Error,match='storage_required'):
        plan(Selection(apps=['jellyfin']),inventory(tmp_path))


def test_selection_rejects_arbitrary_commands():
    Selection,*_=modules()
    with pytest.raises(ValueError): Selection(apps=['sh; rm -rf /'])
    with pytest.raises(ValueError): Selection(apps=['tailscale'],command='id')


def test_cpu_rejects_ml_but_allows_light_profile(tmp_path):
    Selection,plan,Error,*_=modules(); inv=inventory(tmp_path)
    p=plan(Selection(apps=['immich'],volume='disk-a',database_volume='disk-a',machine_learning=False),inv)
    assert p['apps']==['immich']
    with pytest.raises(Error,match='cpu_ml_unsupported'):
        plan(Selection(apps=['immich'],volume='disk-a',database_volume='disk-a',machine_learning=True),inv)


def test_plan_refuses_unsupported_os_and_missing_mount(tmp_path):
    Selection,plan,Error,_,resolve=modules(); inv=inventory(tmp_path)
    inv['supported']=False
    with pytest.raises(Error,match='unsupported_host'): plan(Selection(apps=['tailscale']),inv)
    with pytest.raises(Error,match='volume_missing'): resolve('disappeared',inv)


def test_storage_refuses_symlink_and_network_filesystem(tmp_path):
    _,_,Error,_,resolve=modules(); inv=inventory(tmp_path)
    link=tmp_path/'link'; link.symlink_to(tmp_path/'disk',target_is_directory=True)
    inv['volumes'][0]['path']=str(link)
    with pytest.raises(Error,match='unsafe_storage'): resolve('disk-a',inv)
    inv['volumes'][0]['path']=str(tmp_path/'disk'); inv['volumes'][0]['fstype']='nfs4'
    with pytest.raises(Error,match='unsafe_storage'): resolve('disk-a',inv)


def test_single_job_and_recovery_survive_new_store(tmp_path):
    _,_,Error,Store,_=modules(); s=Store(tmp_path/'state')
    j=s.create({'apps':['tailscale']},['preflight','tailscale'])
    with pytest.raises(Error,match='job_active'): s.create({},[])
    s.step(j['id'],'preflight','succeeded')
    reopened=Store(tmp_path/'state'); reopened.interrupt_running()
    job=reopened.get(j['id'])
    assert job['status']=='interrupted'
    assert job['steps'][0]['status']=='succeeded'
    assert job['selection']=={'apps':['tailscale']}


def test_competing_stores_cannot_start_two_jobs(tmp_path):
    _,_,Error,Store,_=modules(); stores=[Store(tmp_path/'state'),Store(tmp_path/'state')]
    results=[]
    def go(s):
        try: results.append(s.create({},[])['id'])
        except Error: results.append('blocked')
    threads=[threading.Thread(target=go,args=(s,)) for s in stores]
    for t in threads:t.start()
    for t in threads:t.join()
    assert results.count('blocked')==1
