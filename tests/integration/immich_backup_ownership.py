"""Real root/UID regression for pg_basebackup-readable HBA backups; VM only."""
import json, os, socket, subprocess, tempfile
from pathlib import Path
from nas_setup.system import System
from nas_setup.immich import prepare_database
assert os.geteuid()==0 and socket.gethostname()=='nas-v02-vm'
assert Path('/root/NAS_SETUP_DISPOSABLE_VM').is_file()
with tempfile.TemporaryDirectory(prefix='immich-owner-test-') as tmp:
    root=Path(tmp);system=System(root/'worker');project=system.project('immich');project.mkdir()
    data=root/'pg';data.mkdir();hba=data/'pg_hba.conf';hba.write_text('local all all trust\n');hba.chmod(0o600)
    os.chown(hba,999,999)
    (project/'compose.yml').write_text(json.dumps({'services': {'database': {'volumes': [
        {'type':'bind','source':str(data),'target':'/var/lib/postgresql/data'}]}}}))
    facts={'databases':['postgres','template0','template1'],'objects':0,'roles':['postgres'],
           'password_set':True,'hba':'/var/lib/postgresql/data/pg_hba.conf'}
    def run(args,**kwargs):
        if args[-1]=='/proc/1/comm':value='postgres\n'
        elif 'json_build_object' in args[-1]:value=json.dumps(facts)
        elif 'template1' in args:value='0\n'
        else:value='1\n'
        return subprocess.CompletedProcess(args,0,value,'')
    system.run=run # Only command boundary; real root-owned host file operations.
    prepare_database(system)
    backup=data/'pg_hba.conf.nas-setup-before-recovery'
    assert backup.read_bytes()==b'local all all trust\n'
    assert (backup.stat().st_uid,backup.stat().st_gid)==(999,999)
    assert backup.stat().st_mode & 0o777==0o600
    assert (hba.stat().st_uid,hba.stat().st_gid)==(999,999)
print('PASS recovery backup preserves PostgreSQL ownership and private mode')
