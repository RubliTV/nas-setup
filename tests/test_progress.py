import json
import threading
import pytest
from nas_setup.jobs import JobStore


def test_step_timer_and_activity_survive_reload_and_reset_on_retry(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr('nas_setup.jobs.time.time', lambda: now[0])
    store = JobStore(tmp_path)
    job = store.create({}, ['immich'])
    store.step(job['id'], 'immich', 'running')
    assert store.get()['steps'][0]['started_at'] == 1000
    now[0] = 1010
    store.activity(job['id'], 'immich', 'downloading_app')
    current = JobStore(tmp_path).get()
    assert current['steps'][0]['started_at'] == 1000
    assert current['steps'][0]['activity'] == 'downloading_app'
    store.step(job['id'], 'immich', 'failed')
    now[0] = 1020
    store.step(job['id'], 'immich', 'running')
    assert store.get()['steps'][0]['started_at'] == 1020
    assert store.get()['steps'][0].get('activity') is None


def test_activity_is_allowlisted_scoped_and_not_shared_between_threads(tmp_path):
    from nas_setup.progress import report, reporting
    store = JobStore(tmp_path); job = store.create({}, ['immich'])
    store.step(job['id'], 'immich', 'running')
    with reporting(lambda code: store.activity(job['id'], 'immich', code)):
        report('downloading_app')
        thread = threading.Thread(target=lambda: report('checking_app'))
        thread.start(); thread.join()
        with pytest.raises(ValueError): report('password=SECRET')
    report('starting_app')
    assert store.get()['steps'][0]['activity'] == 'downloading_app'
    assert 'SECRET' not in json.dumps(store.get())
    store.step(job['id'], 'immich', 'succeeded')
    store.activity(job['id'], 'immich', 'checking_app')
    assert store.get()['steps'][0].get('activity') is None


def test_engine_exposes_live_activity_before_step_completes(tmp_path):
    from nas_setup.engine import Engine
    from nas_setup.system import DemoSystem
    from nas_setup.progress import report
    entered = threading.Event(); release = threading.Event()
    class SlowApp(DemoSystem):
        def install_app(self, app, selection):
            report('downloading_app'); entered.set()
            assert release.wait(5)
    engine = Engine(tmp_path/'worker', SlowApp(tmp_path/'demo', delay=.001))
    engine.call('start', {'apps':['jellyfin'], 'volume':'demo-data'})
    try:
        assert entered.wait(5)
        snapshot = engine.call('job')
        step = next(s for s in snapshot['steps'] if s['id']=='jellyfin')
        assert snapshot['status']=='running' and step['status']=='running'
        assert step['activity']=='downloading_app'
        assert snapshot['observed_at'] >= step['started_at']
    finally:
        release.set()
