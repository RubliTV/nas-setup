import importlib.util
import json
import time
from pathlib import Path
import pytest


def load():
    assert importlib.util.find_spec('nas_setup.engine'), 'Installation engine is not implemented'
    from nas_setup.engine import Engine
    from nas_setup.system import DemoSystem, write_secret, render_compose
    from nas_setup.models import SetupError, Selection
    return Engine,DemoSystem,write_secret,render_compose,SetupError,Selection


def wait(engine):
    for _ in range(200):
        job=engine.call('job',{})
        if job and job['status']!='running':return job
        time.sleep(.02)
    pytest.fail('job did not finish')


def test_simulated_install_is_durable_and_has_verified_result(tmp_path):
    Engine,Demo,*_=load(); system=Demo(tmp_path/'demo'); engine=Engine(tmp_path/'worker',system)
    selection={'apps':['jellyfin'],'volume':'demo-data'}
    assert engine.call('plan',selection)['steps']==['preflight','docker','jellyfin','verify']
    engine.call('start',selection); job=wait(engine)
    assert job['status']=='succeeded'
    assert all(s['status']=='succeeded' for s in job['steps'])
    assert Engine(tmp_path/'worker',system).call('job',{})['id']==job['id']


def test_secret_is_preserved_and_private(tmp_path):
    _,_,secret,*_=load(); path=tmp_path/'secret'
    a=secret(path);b=secret(path)
    assert a==b and len(a)>=32
    assert path.stat().st_mode & 0o077==0


def test_compositions_have_no_restart_bypass_or_implicit_host_paths(tmp_path):
    _,_,_,render,_,Selection=load()
    s=Selection(apps=['immich'],volume='a',database_volume='b',machine_learning=False)
    config=render('immich',s,tmp_path/'data',tmp_path/'database','secret')
    assert 'immich-machine-learning' not in config['services']
    for svc in config['services'].values():
        assert svc['restart']=='no'
        for v in svc.get('volumes',[]):
            if v['type']=='bind':assert v['bind']['create_host_path'] is False
    assert config['services']['immich-server']['environment']['IMMICH_MACHINE_LEARNING_ENABLED']=='false'


def test_unknown_operation_and_payload_are_rejected(tmp_path):
    Engine,Demo,_,_,Error,_=load(); e=Engine(tmp_path/'worker',Demo(tmp_path/'demo'))
    with pytest.raises(Error,match='operation_invalid'):e.call('exec',{'command':'id'})
    with pytest.raises(ValueError):e.call('start',{'apps':['tailscale'],'command':'id'})


def test_missing_storage_during_job_fails_before_app_install(tmp_path):
    Engine,Demo,_,_,Error,_=load()
    class DisappearingDisk(Demo):
        def install_docker(self,selection):
            super().install_docker(selection); self.disk_available=False
    system=DisappearingDisk(tmp_path/'demo');e=Engine(tmp_path/'worker',system)
    e.call('start',{'apps':['jellyfin'],'volume':'demo-data'})
    job=wait(e)
    assert job['status']=='failed' and job['error']=='volume_missing'
    assert 'jellyfin' not in system.installed


def test_retry_retains_job_identity_and_completed_work(tmp_path):
    Engine,Demo,_,_,Error,_=load()
    class Flaky(Demo):
        def install_app(self,app,selection):
            if not hasattr(self,'failed'):
                self.failed=True; raise Error('download_failed')
            return super().install_app(app,selection)
    system=Flaky(tmp_path/'demo');e=Engine(tmp_path/'worker',system)
    original=e.call('start',{'apps':['jellyfin'],'volume':'demo-data'}); assert wait(e)['status']=='failed'
    e.call('resume',{}); result=wait(e)
    assert result['status']=='succeeded' and result['id']==original['id']


def test_pause_is_at_step_boundary(tmp_path):
    Engine,Demo,*_=load(); e=Engine(tmp_path/'worker',Demo(tmp_path/'demo',delay=.15))
    e.call('start',{'apps':['tailscale']});e.call('pause',{})
    assert wait(e)['status']=='paused'
