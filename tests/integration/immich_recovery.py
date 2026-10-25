"""Pinned-image startup/recovery regression; ONLY the dedicated disposable VM."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import yaml
from nas_setup.system import System, render_compose, write_secret
from nas_setup.models import Selection, SetupError
from nas_setup.immich import prepare_database, TCP_CHECK

if os.geteuid()!=0 or socket.gethostname()!='nas-v02-vm' or not Path('/root/NAS_SETUP_DISPOSABLE_VM').is_file():
    raise SystemExit('Dedicated disposable VM required')

def run(args, timeout=300, check=True):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if check and p.returncode: raise RuntimeError(p.stdout[-2000:]+'\n'+p.stderr[-2000:])
    return p

def wait_sql(args):
    for _ in range(300):
        p=run(args+['SELECT 1'],check=False)
        if p.returncode==0:return
        time.sleep(2)
    raise AssertionError('PostgreSQL did not start')

system=System()
project=system.project('immich')
if project.exists():raise SystemExit('Requires an unused VM Immich project; no existing app data changed')
project.mkdir(mode=0o700)
volume=system.inventory()['volumes'][0]['id']
selection=Selection(apps=['immich'],volume=volume,database_volume=volume,machine_learning=False)
data,db=system._storage('immich',selection)
secret=write_secret(project/'db-password')
config=render_compose('immich',selection,data,db,secret,write_secret(system.state/'installation-id'))
config['services']['immich-server']['depends_on']=['redis','database']  # saved v0.2.1 composition
(project/'compose.yml').write_text(yaml.safe_dump(config));(project/'compose.yml').chmod(0o600)
(project/'selection.json').write_text(selection.model_dump_json())
(project/'mounts.json').write_text(json.dumps(system.selected_mounts('immich',selection)))
compose=['docker','compose','-f',str(project/'compose.yml')]
run(compose+['pull','database','redis'],timeout=1800)
print('PASS pinned database/cache images downloaded',flush=True)
# Reproduce the persisted state from an exit between initdb and the remaining
# official entrypoint steps: no host HBA rule, no application database.
script='''source /usr/local/bin/docker-entrypoint.sh
docker_setup_env
docker_create_db_directories
exec gosu postgres bash -ec 'source /usr/local/bin/docker-entrypoint.sh; docker_setup_env; docker_init_database_dir'
'''
run(compose+['run','--rm','--no-deps','--entrypoint','bash','database','-ec',script],timeout=600)
run(compose+['up','-d','--no-deps','database'])
sql=compose+['exec','-T','--user','postgres','database','psql','-X','-w','-U','postgres','-d','postgres','-Atqc']
wait_sql(sql)
assert run(sql+["SELECT count(*) FROM pg_database WHERE datname='immich'"]).stdout.strip()=='0'
failed=run(compose+['exec','-T','--user','postgres','database','sh','-ec',TCP_CHECK],check=False)
assert failed.returncode and 'pg_hba.conf' in failed.stderr
print('PASS reproduced missing Immich DB and refused host authentication',flush=True)
hba=db/'postgres/pg_hba.conf';before=hba.read_bytes()
# The SQL queries must detect custom names beginning with pg (no underscore).
for create,remove,connection in [
    ('CREATE ROLE pgcustom','DROP ROLE pgcustom',sql),
    ('CREATE SCHEMA pgdata; CREATE TABLE pgdata.sentinel (id int)','DROP TABLE pgdata.sentinel; DROP SCHEMA pgdata',sql),
    ('CREATE TABLE public.sentinel (id int)','DROP TABLE public.sentinel',sql[:-2]+['template1','-Atqc']),
]:
    run(connection+[create])
    try:prepare_database(system)
    except SetupError as exc:assert str(exc)=='immich_database_recovery_required',str(exc)
    else:raise AssertionError('Unexpected cluster was adopted')
    assert hba.read_bytes()==before
    run(connection+[remove])
print('PASS unexpected roles, schemas and template tables blocked without HBA changes',flush=True)
version=(db/'postgres/PG_VERSION').read_bytes()
system.guard_stack('immich');system.check_project_ownership('immich')
prepare_database(system)
assert run(compose+['exec','-T','--user','postgres','database','sh','-ec',TCP_CHECK]).stdout.strip()=='1'
assert (db/'postgres/pg_hba.conf.nas-setup-before-recovery').read_bytes()==before
assert (db/'postgres/pg_hba.conf.nas-setup-before-recovery').stat().st_uid==hba.stat().st_uid
wrong=run(compose+['exec','-T','--user','postgres','database','sh','-ec',TCP_CHECK.replace('$POSTGRES_PASSWORD','deliberately-wrong-test-password')],check=False)
assert wrong.returncode and 'password authentication failed' in wrong.stderr
print('PASS wrong password refused; recovery does not enable host trust',flush=True)
assert (db/'postgres/PG_VERSION').read_bytes()==version
repaired=hba.read_bytes()
prepare_database(system)
assert hba.read_bytes()==repaired
assert (db/'postgres/pg_hba.conf.nas-setup-before-recovery').read_bytes()==before
print('PASS recovery, password-authenticated TCP and idempotent retry; original HBA preserved',flush=True)
run(compose+['pull','immich-server'],timeout=1800)
# Use the actual stack_run path, including mount and ownership checks.
with open('/root/immich-stack-private.log','w') as log:
    child=subprocess.Popen(['/opt/nas-setup/venv/bin/python','-c','from nas_setup.system import System; System().stack_run("immich")'],stdout=log,stderr=subprocess.STDOUT)
    try:
        system.wait_app('immich',timeout=900)
        print('PASS recovered Immich HTTP /api/server/ping',flush=True)
    finally:
        run(compose+['stop'],timeout=180)
        child.wait(timeout=60)
# Separate fresh bind directory: original recovered data and backup stay intact.
run(compose+['down'],timeout=180)
fresh=db.parent/'immich-fresh-db';fresh.mkdir(mode=0o700);(fresh/'postgres').mkdir(mode=0o700)
config=render_compose('immich',selection,data,fresh,secret,write_secret(system.state/'installation-id'))
(project/'compose.yml').write_text(yaml.safe_dump(config))
with open('/root/immich-fresh-stack-private.log','w') as log:
    child=subprocess.Popen(['/opt/nas-setup/venv/bin/python','-c','from nas_setup.system import System; System().stack_run("immich")'],stdout=log,stderr=subprocess.STDOUT)
    try:
        system.wait_app('immich',timeout=900)
        assert not (fresh/'postgres/pg_hba.conf.nas-setup-before-recovery').exists()
        print('PASS fresh initialization through production stack_run and Immich HTTP',flush=True)
    finally:
        run(compose+['stop'],timeout=180)
        child.wait(timeout=60)
print('IMMICH RECOVERY AND FRESH START PASS',flush=True)
