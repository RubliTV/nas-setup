"""Check packaged v0.2.1 -> v0.2.2 update before running the Immich regression."""
import hashlib, json, os, socket, sqlite3, subprocess, sys, tarfile, tempfile
from pathlib import Path
if os.geteuid()!=0 or socket.gethostname()!='nas-v02-vm' or not Path('/root/NAS_SETUP_DISPOSABLE_VM').is_file():
    raise SystemExit('Dedicated disposable VM required')
archive=Path(sys.argv[1]);digest=sys.argv[2]
assert hashlib.sha256(archive.read_bytes()).hexdigest()==digest

def jobs():
    with sqlite3.connect('file:/var/lib/nas-setup/worker/jobs.sqlite?mode=ro',uri=True) as db:
        return db.execute('SELECT id,body,created FROM jobs ORDER BY id').fetchall()

def configuration():
    return {f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in Path('/etc/nas-setup').iterdir() if f.is_file()}

before_jobs=jobs();before_config=configuration()
installed=subprocess.check_output(['/opt/nas-setup/venv/bin/python','-c','from importlib.metadata import version; print(version("nas-setup"))'],text=True).strip()
with tempfile.TemporaryDirectory(prefix='nas-v022-',dir='/root') as tmp:
    with tarfile.open(archive) as bundle:bundle.extractall(tmp,filter='data')
    fd=os.open('/root/nas-v022-update-'+digest[:12]+'-private.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as log:
        result=subprocess.run(['bash',tmp+'/nas-setup-0.2.2/scripts/bootstrap.sh','--update'],stdout=log,stderr=subprocess.STDOUT,timeout=1800)
    assert result.returncode==0,'Update failed; inspect private log in VM'
assert jobs()==before_jobs,'Saved job changed'
assert configuration()==before_config,'Certificate/configuration changed'
subprocess.run(['/opt/nas-setup/venv/bin/python','-c','from importlib.metadata import version; assert version("nas-setup")=="0.2.2"'],check=True)
print(f'PASS packaged {installed} -> 0.2.2 update; saved job, certificate and configuration unchanged',flush=True)
if '--skip-app-test' not in sys.argv:
    subprocess.run(['/opt/nas-setup/venv/bin/python','/root/immich_recovery.py'],check=True)
