"""Regressions for a server exit interrupting PostgreSQL's first initialization."""
import json
from pathlib import Path
import subprocess
import pytest
from nas_setup.models import Selection, SetupError
from nas_setup.system import System, render_compose


def test_server_waits_for_database_and_cache_health(tmp_path):
    config = render_compose('immich', Selection(apps=['immich'], volume='a', database_volume='a'), tmp_path/'data', tmp_path/'db', 'secret')
    assert config['services']['immich-server']['depends_on'] == {
        'database': {'condition': 'service_healthy'}, 'redis': {'condition': 'service_healthy'}}


def fixture(tmp_path):
    system = System(tmp_path/'worker')
    project = system.project('immich'); project.mkdir()
    data = tmp_path/'pg'; data.mkdir()
    hba = data/'pg_hba.conf'; hba.write_text('# original\nlocal all all trust\nhost all all 127.0.0.1/32 trust\n'); hba.chmod(0o600)
    (project/'compose.yml').write_text(json.dumps({'services': {'database': {'volumes': [
        {'type': 'bind', 'source': str(data), 'target': '/var/lib/postgresql/data'}]}}}))
    return system, hba


def test_partial_initialization_is_completed_without_replacing_database_files(tmp_path):
    from nas_setup.immich import prepare_database
    system, hba = fixture(tmp_path)
    original = hba.read_bytes()
    sentinel = hba.parent/'PG_VERSION'; sentinel.write_text('14\n')
    commands = []
    facts = {'databases': ['postgres', 'template0', 'template1'], 'objects': 0, 'roles': ['postgres'],
             'password_set': True, 'hba': '/var/lib/postgresql/data/pg_hba.conf'}
    def run(args, **kwargs):
        commands.append(args)
        if 'template1' in args: return subprocess.CompletedProcess(args, 0, '0\n', '')
        if args[-1] == '/proc/1/comm': return subprocess.CompletedProcess(args, 0, 'postgres\n', '')
        if 'PGPASSWORD=' in args[-1]: return subprocess.CompletedProcess(args, 0, '1\n', '')
        if args[-1] == 'SELECT 1': return subprocess.CompletedProcess(args, 0, '1\n', '')
        if 'json_build_object' in args[-1]: return subprocess.CompletedProcess(args, 0, json.dumps(facts), '')
        return subprocess.CompletedProcess(args, 0, '', '')
    system.run = run
    prepare_database(system)
    assert sentinel.read_text() == '14\n'
    assert hba.read_bytes().startswith(original)
    assert 'host immich postgres samenet scram-sha-256' in hba.read_text()
    assert (hba.parent/'pg_hba.conf.nas-setup-before-recovery').read_bytes() == original
    restart = next(i for i, a in enumerate(commands) if 'restart' in a)
    create = next(i for i, a in enumerate(commands) if a[-1] == 'CREATE DATABASE immich OWNER postgres')
    assert restart < create
    assert any(a[-1] == 'SELECT 1' for a in commands[restart+1:create])
    assert not any('pg_reload_conf' in a[-1] for a in commands)
    assert any('PGPASSWORD=' in a[-1] for a in commands)
    # A retry after successful creation must not change authentication or issue CREATE again.
    facts['databases'].append('immich')
    commands.clear(); before = hba.read_bytes()
    prepare_database(system)
    assert hba.read_bytes() == before
    assert not any(a[-1].startswith('CREATE DATABASE') for a in commands)
    assert not any('restart' in a for a in commands)


@pytest.mark.parametrize('override', [
    {'databases': ['postgres', 'template0', 'template1', 'other']},
    {'objects': 1}, {'roles': ['postgres', 'other']}, {'password_set': False},
    {'hba': '/elsewhere/pg_hba.conf'},
])
def test_unexpected_existing_cluster_is_never_repaired(tmp_path, override):
    from nas_setup.immich import prepare_database
    system, hba = fixture(tmp_path)
    facts = {'databases': ['postgres', 'template0', 'template1'], 'objects': 0, 'roles': ['postgres'],
             'password_set': True, 'hba': '/var/lib/postgresql/data/pg_hba.conf', **override}
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(facts) if 'json_build_object' in args[-1] else ('postgres\n' if args[-1] == '/proc/1/comm' else '1\n'), '')
    system.run = run
    before = hba.read_bytes()
    with pytest.raises(SetupError, match='immich_database_recovery_required'): prepare_database(system)
    assert hba.read_bytes() == before
    assert not any(a[-1].startswith('CREATE DATABASE') for a in commands)


def test_stack_waits_for_database_before_launching_server(tmp_path, monkeypatch):
    system, _ = fixture(tmp_path)
    (system.project('immich')/'selection.json').write_text(Selection(apps=['immich'], volume='a', database_volume='a').model_dump_json())
    events = []
    monkeypatch.setattr(system, 'guard_stack', lambda app: events.append('mounts'))
    monkeypatch.setattr(system, 'check_project_ownership', lambda app: events.append('ownership'))
    monkeypatch.setattr(system, 'prepare_immich_database', lambda: events.append('database-ready'), raising=False)
    monkeypatch.setattr('os.execv', lambda *args: events.append('server'))
    system.stack_run('immich')
    assert events == ['mounts', 'ownership', 'database-ready', 'server']


def test_final_server_identity_is_checked_before_sql_readiness(tmp_path, monkeypatch):
    from nas_setup.immich import prepare_database
    system, _ = fixture(tmp_path)
    final_server = False
    facts = {'databases': ['postgres', 'template0', 'template1', 'immich']}
    def run(args, **kwargs):
        nonlocal final_server
        if args[-1] == '/proc/1/comm':
            final_server = True
            return subprocess.CompletedProcess(args, 0, 'postgres\n', '')
        if args[-1] == 'SELECT 1':
            assert final_server, 'Temporary init server must not satisfy the final-server readiness gate'
        return subprocess.CompletedProcess(args, 0, json.dumps(facts) if 'json_build_object' in args[-1] else '1\n', '')
    system.run = run
    prepare_database(system)


def test_customized_template_is_not_used_to_complete_partial_init(tmp_path):
    from nas_setup.immich import prepare_database
    system, hba = fixture(tmp_path)
    before = hba.read_bytes()
    facts = {'databases': ['postgres', 'template0', 'template1'], 'objects': 0, 'roles': ['postgres'],
             'password_set': True, 'hba': '/var/lib/postgresql/data/pg_hba.conf'}
    def run(args, **kwargs):
        if args[-1] == '/proc/1/comm': value = 'postgres\n'
        elif 'json_build_object' in args[-1]: value = json.dumps(facts)
        elif 'template1' in args: value = '1\n' # user object present
        else: value = '1\n'
        return subprocess.CompletedProcess(args, 0, value, '')
    system.run = run
    with pytest.raises(SetupError, match='immich_database_recovery_required'): prepare_database(system)
    assert hba.read_bytes() == before


def test_interrupted_restart_retries_before_creating_database(tmp_path):
    from nas_setup.immich import prepare_database
    system, hba = fixture(tmp_path)
    original = hba.read_bytes()
    facts = {'databases': ['postgres', 'template0', 'template1'], 'objects': 0, 'roles': ['postgres'],
             'password_set': True, 'hba': '/var/lib/postgresql/data/pg_hba.conf'}
    commands = []; interrupt = True
    def run(args, **kwargs):
        commands.append(args)
        if 'restart' in args and interrupt: raise SetupError('command_failed')
        if args[-1] == '/proc/1/comm': value = 'postgres\n'
        elif 'json_build_object' in args[-1]: value = json.dumps(facts)
        elif 'template1' in args: value = '0\n'
        else: value = '1\n'
        return subprocess.CompletedProcess(args, 0, value, '')
    system.run = run
    with pytest.raises(SetupError): prepare_database(system)
    assert not any(a[-1].startswith('CREATE DATABASE') for a in commands)
    edited = hba.read_bytes()
    interrupt = False; commands.clear()
    prepare_database(system)
    assert hba.read_bytes() == edited
    assert hba.with_name('pg_hba.conf.nas-setup-before-recovery').read_bytes() == original
    restart = next(i for i, a in enumerate(commands) if 'restart' in a)
    create = next(i for i, a in enumerate(commands) if a[-1].startswith('CREATE DATABASE'))
    assert restart < create


def test_partial_existing_backup_blocks_hba_replacement(tmp_path):
    from nas_setup.immich import prepare_database
    system, hba = fixture(tmp_path)
    original = hba.read_bytes()
    backup = hba.with_name('pg_hba.conf.nas-setup-before-recovery'); backup.write_bytes(b'# partial')
    facts = {'databases': ['postgres', 'template0', 'template1'], 'objects': 0, 'roles': ['postgres'],
             'password_set': True, 'hba': '/var/lib/postgresql/data/pg_hba.conf'}
    def run(args, **kwargs):
        if args[-1] == '/proc/1/comm': value = 'postgres\n'
        elif 'json_build_object' in args[-1]: value = json.dumps(facts)
        elif 'template1' in args: value = '0\n'
        else: value = '1\n'
        return subprocess.CompletedProcess(args, 0, value, '')
    system.run = run
    with pytest.raises(SetupError, match='immich_database_recovery_required'): prepare_database(system)
    assert hba.read_bytes() == original
    assert backup.read_bytes() == b'# partial'
