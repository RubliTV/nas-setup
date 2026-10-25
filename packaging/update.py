"""Replace an installed runtime without changing NAS configuration or job records."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import time
import urllib.request
import tempfile
import tomllib
import uuid


class UpdateError(RuntimeError):
    pass


def version(source):
    try:
        return tomllib.loads((source / 'pyproject.toml').read_text())['project']['version']
    except (OSError, ValueError, KeyError) as exc:
        raise UpdateError('Cannot read installed/release version; no update performed.') from exc


def check_jobs(connection):
    try:
        for (body,) in connection.execute('SELECT body FROM jobs'):
            job = json.loads(body)
            if job.get('status') in {'running', 'pending'} or any(step.get('status') == 'running' for step in job.get('steps', [])):
                raise UpdateError('An active or stale running job prevents this update. Wait for the job to finish or pause safely; job state was not changed.')
            if job.get('status') not in {'failed', 'paused', 'interrupted', 'waiting-for-user', 'waiting-for-sync', 'succeeded'}:
                raise UpdateError('Unrecognized job state; refusing to stop the worker.')
    except (sqlite3.Error, ValueError, TypeError, AttributeError) as exc:
        raise UpdateError('Cannot safely inspect job state; no update performed.') from exc


@contextmanager
def job_database(state, *, lock=False):
    database = state / 'worker/jobs.sqlite'
    connection = None
    try:
        # Never create a missing DB or run JobStore/Engine initialization, which
        # would modify stale running records. The write reservation below makes
        # queued start/resume requests wait; no SQL writes are performed here.
        connection = sqlite3.connect(database.resolve().as_uri() + ('?mode=rw' if lock else '?mode=ro'), uri=True, timeout=10)
        if lock:
            connection.execute('BEGIN IMMEDIATE')
        check_jobs(connection)
        yield connection
    except sqlite3.Error as exc:
        raise UpdateError('Cannot safely inspect/lock the job database. Start NAS Setup once if its database is missing; no jobs were changed.') from exc
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()


def wait_ready(conf, timeout=30):
    config = json.loads((conf / 'config.json').read_text())
    context = ssl.create_default_context(cafile=str(conf / 'server.crt'))
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(config['origin'] + '/api/session', context=context, timeout=2) as response:
                body = json.loads(response.read(65536))
                if response.status == 200 and body.get('authenticated') is False and body.get('demo') is False:
                    return
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise UpdateError('Updated HTTPS interface did not become ready with the existing certificate.')
        time.sleep(0.25)


def update_release(source, *, destination=Path('/opt/nas-setup'), state=Path('/var/lib/nas-setup'), conf=Path('/etc/nas-setup'), run, probe=None):
    source, destination, state = Path(source), Path(destination), Path(state)
    if not destination.is_dir():
        raise UpdateError('NAS Setup is not installed. Run the installer without --update.')
    installed_version = version(destination / 'source')
    if installed_version not in {'0.2.0', '0.2.1', '0.2.2', '0.2.3', '0.2.4'} or version(source) != '0.2.4':
        raise UpdateError('Unsupported update version: only 0.2.0/0.2.1/0.2.2/0.2.3/0.2.4 to 0.2.4 is supported.')
    with job_database(state):
        pass
    # Initial eligibility checks above precede even creation of the update lock.
    with (destination.parent / '.nas-setup-update.lock').open('a') as update_lock:
        try:
            fcntl.flock(update_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError('Another NAS Setup update is already in progress.') from exc
        with job_database(state):
            pass
        return _replace_runtime(source, destination, state, installed_version, run, probe or (lambda: wait_ready(Path(conf))))


def _replace_runtime(source, destination, state, installed_version, run, probe):
    services = ('nas-setup-worker', 'nas-setup-web')
    active, enabled = {}, {}
    for service in services:
        status = run('systemctl', 'show', '-p', 'ActiveState', '--value', service).strip()
        if status not in {'active', 'inactive', 'failed'}:
            raise UpdateError('A NAS Setup service is transitioning; try again when it is stable.')
        active[service] = status == 'active'
        enabled[service] = run('systemctl', 'show', '-p', 'UnitFileState', '--value', service).strip()
        if enabled[service] not in {'enabled', 'disabled'}:
            raise UpdateError('Unsupported NAS Setup service enablement state; no update performed.')
    previous_umask = os.umask(0o022)
    staged = None
    backup = destination.parent / ('nas-setup-backup-' + installed_version + '-' + uuid.uuid4().hex)
    stopped = []
    backed_up = False
    linked = False
    reopen_attempted = False
    try:
        # The versioned directory is permanent: pip console-script shebangs
        # continue pointing at this exact venv after the stable symlink switches.
        staged = Path(tempfile.mkdtemp(prefix='nas-setup-runtime-0.2.4-', dir=destination.parent))
        staged.chmod(0o755)
        shutil.copytree(source, staged / 'source')
        for path in (staged / 'source').rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        run('python3', '-m', 'venv', str(staged / 'venv'))
        python = str(staged / 'venv/bin/python')
        run(python, '-m', 'pip', 'install', '-r', str(staged / 'source/requirements.lock'))
        run(python, '-m', 'pip', 'install', '--no-deps', str(staged / 'source'))
        run(python, '-c', 'from importlib.metadata import version; import nas_setup.cli, nas_setup.worker; assert version("nas-setup") == "0.2.4"')
        # Reject jobs that began during dependency installation before downtime.
        with job_database(state):
            pass
        stopped.append('nas-setup-web')
        run('systemctl', 'stop', 'nas-setup-web')
        # Once web ingress is stopped, reserve the DB writer lock, recheck, and
        # hold it until worker exit. Every start/resume commits running before
        # executing a job, so no accepted job can start between check and stop.
        with job_database(state, lock=True):
            stopped.append('nas-setup-worker')
            run('systemctl', 'stop', 'nas-setup-worker')
        with job_database(state):
            pass
        destination.rename(backup)
        backed_up = True
        destination.symlink_to(staged, target_is_directory=True)
        linked = True
        for service in services:
            run('systemctl', 'start', service)
            if run('systemctl', 'show', '-p', 'ActiveState', '--value', service).strip() != 'active':
                raise UpdateError('Updated service did not start.')
        reopen_attempted = True
        output = run(str(destination / 'venv/bin/nas-setup'), 'reopen')
        probe()
        return output
    except BaseException as exc:
        rollback_errors = []
        def restore(action):
            try:
                action()
            except BaseException as failure:
                rollback_errors.append(str(failure))
        safe_to_restore = True
        if backed_up:
            try:
                run('systemctl', 'stop', 'nas-setup-web')
                with job_database(state, lock=True):
                    run('systemctl', 'stop', 'nas-setup-worker')
                    if linked:
                        destination.unlink()
                    backup.rename(destination)
            except BaseException as failure:
                safe_to_restore = False
                rollback_errors.append(str(failure))
                # A user may have resumed work on the new runtime. Never kill
                # that worker to roll back; leave both runtime copies available.
                if active['nas-setup-web']:
                    restore(lambda: run('systemctl', 'start', 'nas-setup-web'))
        if safe_to_restore:
            for service in services:
                if service in stopped:
                    restore(lambda service=service: run('systemctl', 'start' if active[service] else 'stop', service))
                if reopen_attempted:
                    restore(lambda service=service: run('systemctl', 'enable' if enabled[service] == 'enabled' else 'disable', service))
        if staged is not None and not rollback_errors:
            shutil.rmtree(staged)
        if rollback_errors:
            raise UpdateError('Update failed and rollback needs attention. Runtime backup: ' + str(backup) + '. ' + '; '.join(rollback_errors)) from exc
        raise UpdateError('Update aborted; runtime/services rolled back. ' + str(exc)) from exc
    finally:
        os.umask(previous_umask)
