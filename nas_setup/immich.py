"""Initialize the managed database before Immich can terminate the Compose stack.

Recovery only completes a pristine PostgreSQL cluster left by interrupted initdb;
it never deletes a cluster, resets a password, or changes an existing Immich DB.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

import yaml
from .models import SetupError


FACTS = """SELECT json_build_object(
 'databases', (SELECT json_agg(datname ORDER BY datname) FROM pg_database),
 'objects', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'),
 'roles', (SELECT json_agg(rolname ORDER BY rolname) FROM pg_roles WHERE left(rolname, 3) <> 'pg_'),
 'password_set', (SELECT rolpassword IS NOT NULL FROM pg_authid WHERE rolname='postgres'),
 'hba', current_setting('hba_file'))"""

# TCP to the container's network address verifies the same password-authenticated
# route used by Immich, not the temporary socket-only server used during initdb.
TCP_CHECK = '''PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=3 psql -X -w -h database -U postgres -d immich -Atqc 'SELECT 1' '''


def wait_database(system, compose, sql):
    deadline = time.monotonic() + 600
    while True:
        # The temporary initdb server also accepts local SQL. Check PID 1 first,
        # then SQL, so that a temporary-server response cannot satisfy this gate.
        result = system.run(compose + ['exec', '-T', 'database', 'cat', '/proc/1/comm'], check=False)
        if result.returncode == 0 and result.stdout.strip() == 'postgres':
            result = system.run(sql + ['SELECT 1'], check=False, timeout=15)
            if result.returncode == 0 and result.stdout.strip() == '1':
                break
        if time.monotonic() >= deadline:
            system.diagnostic('immich-database', result.stderr)
            raise SetupError('immich_database_failed')
        time.sleep(2)


def prepare_database(system):
    project = system.project('immich')
    compose = ['docker', 'compose', '-f', str(project/'compose.yml')]
    # Called only after stack_run verifies mount identities and container ownership.
    # Detached dependencies cannot be killed by an early server exit during initdb.
    system.run(compose + ['up', '-d', '--no-deps', 'database', 'redis'], timeout=180)
    sql = compose + ['exec', '-T', '--user', 'postgres', 'database',
                     'psql', '-X', '-w', '-U', 'postgres', '-d', 'postgres', '-Atqc']
    wait_database(system, compose, sql)
    facts = json.loads(system.run(sql + [FACTS]).stdout)
    if 'immich' not in facts['databases']:
        if (set(facts['databases']) != {'postgres', 'template0', 'template1'}
                or facts['objects'] != 0 or facts['roles'] != ['postgres']
                or facts['password_set'] is not True
                or facts['hba'] != '/var/lib/postgresql/data/pg_hba.conf'):
            raise SetupError('immich_database_recovery_required')
        template_sql = sql[:-2] + ['template1', '-Atqc']
        # CREATE DATABASE copies template1; a customized template is not pristine.
        objects = system.run(template_sql + ["SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'"]).stdout.strip()
        if objects != '0':
            raise SetupError('immich_database_recovery_required')
        config = yaml.safe_load((project/'compose.yml').read_text())
        mounts = [v for v in config['services']['database']['volumes']
                  if v.get('type') == 'bind' and v['target'] == '/var/lib/postgresql/data']
        if len(mounts) != 1:
            raise SetupError('unsafe_storage')
        hba = Path(mounts[0]['source'])/'pg_hba.conf'
        backup = hba.with_name(hba.name + '.nas-setup-before-recovery')
        if hba.resolve() != hba or not hba.is_file() or backup.is_symlink():
            raise SetupError('unsafe_storage')
        before = hba.read_text()
        rule = 'host immich postgres samenet scram-sha-256'
        if rule not in [line.strip() for line in before.splitlines()]:
            if backup.exists() and backup.read_bytes() != hba.read_bytes():
                raise SetupError('immich_database_recovery_required')
            if not backup.exists():
                # Publish only a complete backup; interrupted temporary writes
                # cannot be mistaken for a valid original on the next attempt.
                fd, temporary = tempfile.mkstemp(prefix='.nas-setup-backup-', dir=hba.parent)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        original = hba.stat()
                        os.fchown(stream.fileno(), original.st_uid, original.st_gid)
                        stream.write(hba.read_bytes()); stream.flush(); os.fsync(stream.fileno())
                    os.link(temporary, backup)
                    directory = os.open(hba.parent, os.O_RDONLY)
                    try: os.fsync(directory)
                    finally: os.close(directory)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            fd, name = tempfile.mkstemp(prefix='.nas-setup-hba-', dir=hba.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(before + '\n# NAS Setup: complete interrupted initial setup\n' + rule + '\n')
                    stream.flush(); os.fsync(stream.fileno())
                shutil.copystat(hba, name)
                original = hba.stat()
                os.chown(name, original.st_uid, original.st_gid)
                os.replace(name, hba)
                directory = os.open(hba.parent, os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
            finally:
                Path(name).unlink(missing_ok=True)
        # pgvecto.rs 0.2.0 exits its worker on SIGHUP, so reload is unsafe.
        # Restart before CREATE: an interruption still leaves the DB absent,
        # making a retry repeat this gate without any additional recovery state.
        system.run(compose + ['restart', '--timeout', '120', 'database'], timeout=180)
        wait_database(system, compose, sql)
        system.run(sql + ['CREATE DATABASE immich OWNER postgres'])
        system.diagnostic('immich-database', 'Completed interrupted initialization; original authentication configuration backed up.')
    result = system.run(compose + ['exec', '-T', '--user', 'postgres', 'database', 'sh', '-ec', TCP_CHECK], check=False)
    if result.returncode or result.stdout.strip() != '1':
        system.diagnostic('immich-database', result.stderr)
        raise SetupError('immich_database_failed')
