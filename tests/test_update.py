"""Update safety with real SQLite and files; only host commands are simulated."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def updater():
    path = ROOT / 'packaging/update.py'
    assert path.exists(), 'The in-place updater is not implemented'
    spec = importlib.util.spec_from_file_location('nas_update', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installation(tmp_path):
    destination = tmp_path / 'opt/nas-setup'
    (destination / 'source').mkdir(parents=True)
    (destination / 'source/pyproject.toml').write_text('[project]\nversion = "0.2.0"\n')
    (destination / 'venv/bin').mkdir(parents=True)
    (destination / 'venv/bin/nas-setup').write_text('old runtime')
    state = tmp_path / 'var/lib/nas-setup'
    (state / 'worker').mkdir(parents=True)
    (state / 'web').mkdir()
    (state / 'web/access.json').write_text('existing pairing state')
    (state / 'worker/installed-app').write_text('existing app')
    db = state / 'worker/jobs.sqlite'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL)')
        connection.execute('INSERT INTO jobs VALUES (?, ?, ?)', ('job-one', json.dumps({'id': 'job-one', 'status': 'failed', 'steps': [{'id': 'prepare', 'status': 'failed'}]}), 1))
    source = tmp_path / 'release'
    source.mkdir()
    (source / 'pyproject.toml').write_text('[project]\nversion = "0.2.1"\n')
    (source / 'requirements.lock').write_text('')
    conf = tmp_path / 'etc/nas-setup'
    conf.mkdir(parents=True)
    (conf / 'config.json').write_text('{"origin": "https://192.168.1.2:8443"}')
    (conf / 'server.key').write_bytes(b'existing private fixture')
    (conf / 'server.crt').write_bytes(b'existing certificate fixture')
    link = tmp_path / 'usr/local/bin/nas-setup'
    link.parent.mkdir(parents=True)
    link.symlink_to(destination / 'venv/bin/nas-setup')
    return source, destination, state, conf, link


def snapshot(directory):
    return {str(path.relative_to(directory)): (path.read_bytes(), path.stat().st_mode) for path in directory.rglob('*') if path.is_file()}


def job_status(state, status):
    with sqlite3.connect(state / 'worker/jobs.sqlite') as connection:
        body = json.loads(connection.execute('SELECT body FROM jobs').fetchone()[0])
        body['status'] = status
        connection.execute('UPDATE jobs SET body = ?', (json.dumps(body),))


class Host:
    """External command boundary, including dependency installation and systemd."""
    def __init__(self, destination):
        self.destination = destination
        self.active = {'nas-setup-web': True, 'nas-setup-worker': True}
        self.enabled = dict(self.active)
        self.commands = []
        self.fail_start = False
        self.on_web_stop = None
        self.staged = None

    def run(self, *args):
        self.commands.append(args)
        if args[:3] == ('python3', '-m', 'venv'):
            assert not any(command[:2] == ('systemctl', 'stop') for command in self.commands), 'Dependencies must stage before downtime'
            self.staged = Path(args[3]).parent
            (Path(args[3]) / 'bin').mkdir(parents=True)
            (Path(args[3]) / 'bin/python').write_text('interpreter fixture')
            return ''
        if len(args) > 3 and args[1:4] == ('-m', 'pip', 'install'):
            (self.staged / 'venv/bin/nas-setup').write_text('#!' + str(self.staged / 'venv/bin/python') + '\nnew runtime')
            return ''
        if len(args) > 1 and args[1] == '-c':
            return ''
        if args[:2] == ('systemctl', 'show'):
            values = self.active if 'ActiveState' in args else self.enabled
            value = values[args[-1]]
            return ('active' if value else 'inactive') if values is self.active else ('enabled' if value else 'disabled')
        if args[:2] == ('systemctl', 'stop'):
            self.active[args[-1]] = False
            if args[-1] == 'nas-setup-web' and self.on_web_stop:
                self.on_web_stop()
            return ''
        if args[:2] == ('systemctl', 'start'):
            if self.fail_start and self.destination.is_symlink():
                raise subprocess.CalledProcessError(1, args)
            self.active[args[-1]] = True
            return ''
        if args[:2] in [('systemctl', 'enable'), ('systemctl', 'disable')]:
            self.enabled[args[-1]] = args[1] == 'enable'
            return ''
        if args[-1] == 'reopen':
            self.active = dict.fromkeys(self.active, True)
            self.enabled = dict.fromkeys(self.enabled, True)
            return 'pairing output from CLI'
        raise AssertionError('Unexpected external command: ' + repr(args))


@pytest.mark.parametrize('status', ['running', 'pending'])
def test_active_or_stale_running_job_rejected_before_mutation(installation, status):
    source, destination, state, conf, link = installation
    job_status(state, status)
    before = snapshot(destination.parent.parent)
    host = Host(destination)
    module = updater()
    with pytest.raises(module.UpdateError, match='job'):
        module.update_release(source, destination=destination, state=state, run=host.run, probe=lambda: None)
    assert snapshot(destination.parent.parent) == before
    assert not any(command[:2] == ('systemctl', 'stop') for command in host.commands)


def test_failed_job_upgrade_preserves_state_certificate_and_command_link(installation):
    source, destination, state, conf, link = installation
    before_state, before_conf = snapshot(state), snapshot(conf)
    host = Host(destination)
    output = updater().update_release(source, destination=destination, state=state, run=host.run, probe=lambda: None)
    assert output == 'pairing output from CLI'
    assert snapshot(state) == before_state
    assert snapshot(conf) == before_conf
    assert destination.is_symlink()
    assert 'new runtime' in link.read_text()
    interpreter = Path(link.read_text().splitlines()[0][2:])
    assert interpreter.is_file(), 'The installed console script must reference its permanent venv'
    backups = list(destination.parent.glob('nas-setup-backup-*'))
    assert len(backups) == 1
    assert (backups[0] / 'venv/bin/nas-setup').read_text() == 'old runtime'
    assert all(host.active.values())


def test_start_failure_restores_original_runtime_and_service_state(installation):
    source, destination, state, conf, link = installation
    before_state, before_conf = snapshot(state), snapshot(conf)
    host = Host(destination)
    host.fail_start = True
    module = updater()
    with pytest.raises(module.UpdateError, match='rolled back'):
        module.update_release(source, destination=destination, state=state, run=host.run, probe=lambda: None)
    assert not destination.is_symlink()
    assert link.read_text() == 'old runtime'
    assert snapshot(state) == before_state
    assert snapshot(conf) == before_conf
    assert all(host.active.values())


def test_job_started_during_staging_aborts_without_stopping_worker(installation):
    source, destination, state, _, link = installation
    host = Host(destination)
    host.on_web_stop = lambda: job_status(state, 'running')
    module = updater()
    with pytest.raises(module.UpdateError, match='job'):
        module.update_release(source, destination=destination, state=state, run=host.run, probe=lambda: None)
    assert link.read_text() == 'old runtime'
    assert host.active['nas-setup-web']
    assert ('systemctl', 'stop', 'nas-setup-worker') not in host.commands
    with sqlite3.connect(state / 'worker/jobs.sqlite') as connection:
        assert json.loads(connection.execute('SELECT body FROM jobs').fetchone()[0])['status'] == 'running'


def test_unsupported_installed_version_rejected_before_mutation(installation):
    source, destination, state, _, _ = installation
    (destination / 'source/pyproject.toml').write_text('[project]\nversion = "0.1.0"\n')
    host = Host(destination)
    module = updater()
    with pytest.raises(module.UpdateError, match='version'):
        module.update_release(source, destination=destination, state=state, run=host.run, probe=lambda: None)
    assert not host.commands


def test_installer_update_checks_checksum_before_calling_updater(installation, monkeypatch, capsys):
    import hashlib
    import sys
    source, destination, state, _, _ = installation
    spec = importlib.util.spec_from_file_location('nas_installer_test', ROOT / 'packaging/install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (source / 'SHA256SUMS').write_text(hashlib.sha256(b'original').hexdigest() + '  requirements.lock\n')
    os_release = source / 'os-release'
    os_release.write_text('ID=debian\nVERSION_ID="13"\n')
    monkeypatch.setattr(module, 'ROOT', source)
    monkeypatch.setattr(module, 'Path', lambda value: os_release if value == '/etc/os-release' else Path(value))
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module, 'run', lambda *args: 'amd64' if args[0] == 'dpkg' else '8.5.12')
    monkeypatch.setattr(sys, 'argv', ['install.py', '--update'])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
    assert 'checksum failed' in capsys.readouterr().err


def test_installer_update_reaches_real_updater_without_lan_options(installation, monkeypatch, capsys):
    import hashlib
    import sys
    source, destination, state, conf, link = installation
    spec = importlib.util.spec_from_file_location('nas_installer_success', ROOT / 'packaging/install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (source / 'SHA256SUMS').write_text('\n'.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name for path in source.iterdir()) + '\n')
    os_release = source.parent / 'os-release'
    os_release.write_text('ID=debian\nVERSION_ID="13"\n')
    host = Host(destination)
    def command(*args):
        if args[0] == 'dpkg': return 'amd64'
        if args[0] == 'dpkg-query': return '8.5.12'
        return host.run(*args)
    redirects = {'/etc/os-release': os_release, '/opt/nas-setup': destination, '/var/lib/nas-setup': state, '/etc/nas-setup': conf}
    import ssl, urllib.request
    from io import BytesIO
    response = BytesIO(b'{"authenticated":false,"demo":false}')
    response.status = 200
    monkeypatch.setattr(ssl, 'create_default_context', lambda **kwargs: object())
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *args, **kwargs: response)
    monkeypatch.setattr(module, 'ROOT', source)
    monkeypatch.setattr(module, 'Path', lambda value: redirects.get(str(value), Path(value)))
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module, 'run', command)
    monkeypatch.setattr(sys, 'argv', ['install.py', '--update'])
    module.main()
    assert 'new runtime' in link.read_text()
    assert 'pairing output from CLI' in capsys.readouterr().out


def test_readiness_failure_rolls_back_even_after_systemd_reports_active(installation):
    source, destination, state, _, link = installation
    host = Host(destination)
    module = updater()
    def unavailable():
        raise RuntimeError('HTTPS did not become ready')
    with pytest.raises(module.UpdateError, match='rolled back'):
        module.update_release(source, destination=destination, state=state, run=host.run, probe=unavailable)
    assert link.read_text() == 'old runtime'
    assert all(host.active.values())


def test_rollback_refuses_to_interrupt_job_resumed_on_new_runtime(installation):
    source, destination, state, _, link = installation
    host = Host(destination)
    module = updater()
    def unavailable_after_resume():
        job_status(state, 'running')
        raise RuntimeError('HTTPS probe failed')
    with pytest.raises(module.UpdateError, match='rollback needs attention'):
        module.update_release(source, destination=destination, state=state, run=host.run, probe=unavailable_after_resume)
    assert 'new runtime' in link.read_text()
    assert host.commands.count(('systemctl', 'stop', 'nas-setup-worker')) == 1
    assert host.active['nas-setup-worker']


def test_worker_stop_holds_database_lock_against_new_start(installation):
    source, destination, state, _, _ = installation
    host = Host(destination)
    def command(*args):
        if args == ('systemctl', 'stop', 'nas-setup-worker'):
            with sqlite3.connect(state / 'worker/jobs.sqlite', timeout=0) as connection:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    connection.execute('BEGIN IMMEDIATE')
        return host.run(*args)
    updater().update_release(source, destination=destination, state=state, run=command, probe=lambda: None)
    assert destination.is_symlink()


def test_dependency_failure_leaves_services_and_runtime_untouched(installation):
    source, destination, state, _, link = installation
    host = Host(destination)
    before = snapshot(state)
    def command(*args):
        if len(args) > 3 and args[1:4] == ('-m', 'pip', 'install'):
            raise subprocess.CalledProcessError(1, args)
        return host.run(*args)
    module = updater()
    with pytest.raises(module.UpdateError, match='rolled back'):
        module.update_release(source, destination=destination, state=state, run=command, probe=lambda: None)
    assert link.read_text() == 'old runtime'
    assert snapshot(state) == before
    assert not any(command[:2] == ('systemctl', 'stop') for command in host.commands)
    assert not list(destination.parent.glob('nas-setup-runtime-*'))


def test_failed_reopen_restores_initially_stopped_disabled_services(installation):
    source, destination, state, _, link = installation
    host = Host(destination)
    host.active = dict.fromkeys(host.active, False)
    host.enabled = dict.fromkeys(host.enabled, False)
    def command(*args):
        output = host.run(*args)
        if args[-1] == 'reopen':
            raise subprocess.CalledProcessError(1, args)
        return output
    module = updater()
    with pytest.raises(module.UpdateError, match='rolled back'):
        module.update_release(source, destination=destination, state=state, run=command, probe=lambda: None)
    assert not any(host.active.values())
    assert not any(host.enabled.values())
    assert link.read_text() == 'old runtime'


def test_repeated_update_preserves_previous_runtime_and_job_history(installation):
    source, destination, state, _, link = installation
    before = snapshot(state)
    module = updater()
    module.update_release(source, destination=destination, state=state, run=Host(destination).run, probe=lambda: None)
    previous_runtime = destination.resolve()
    module.update_release(source, destination=destination, state=state, run=Host(destination).run, probe=lambda: None)
    assert destination.resolve() != previous_runtime
    assert any(path.resolve() == previous_runtime for path in destination.parent.glob('nas-setup-backup-*'))
    assert previous_runtime.is_dir()
    assert 'new runtime' in link.read_text()
    assert snapshot(state) == before


def test_https_readiness_retries_connection_and_validates_existing_certificate(installation, monkeypatch):
    from io import BytesIO
    _, _, _, conf, _ = installation
    module = updater()
    attempts = []
    context = object()
    def certificate_context(*, cafile):
        assert Path(cafile) == conf / 'server.crt'
        return context
    def request(url, *, context, timeout):
        attempts.append(url)
        assert context is not None
        assert timeout == 2
        if len(attempts) == 1:
            raise OSError('Starting HTTPS')
        response = BytesIO(b'{"authenticated":false,"demo":false}')
        response.status = 200
        return response
    monkeypatch.setattr(module.ssl, 'create_default_context', certificate_context)
    monkeypatch.setattr(module.urllib.request, 'urlopen', request)
    module.wait_ready(conf, timeout=1)
    assert attempts == ['https://192.168.1.2:8443/api/session'] * 2


def test_https_readiness_rejects_wrong_service_response(installation, monkeypatch):
    from io import BytesIO
    _, _, _, conf, _ = installation
    module = updater()
    monkeypatch.setattr(module.ssl, 'create_default_context', lambda **kwargs: object())
    response = BytesIO(b'{"authenticated":false,"demo":true}')
    response.status = 200
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: response)
    with pytest.raises(module.UpdateError, match='did not become ready'):
        module.wait_ready(conf, timeout=0)
