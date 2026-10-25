"""Verify the final patch runtime on the dedicated, already configured VM."""
import argparse, hashlib, http.cookiejar, json, os, re, socket, sqlite3
import ssl, subprocess, tarfile, tempfile, time, urllib.request
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('archive');p.add_argument('sha256');a=p.parse_args()
if os.geteuid()!=0 or socket.gethostname()!='nas-v02-vm' or not Path('/root/NAS_SETUP_DISPOSABLE_VM').is_file():
    raise SystemExit('Disposable VM required')
def run(args,timeout=60):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=timeout).stdout
serials={d.get('serial') for d in json.loads(run(['lsblk','--json','--output','TYPE,SERIAL']))['blockdevices'] if d['type']=='disk'}
if serials!={'NASV02SYSTEM','NASV02DATA1','NASV02DATA2','NASV02DATA3','NASV02DATA4'}:raise SystemExit('Unexpected disks')
def saved_job():
    with sqlite3.connect('file:/var/lib/nas-setup/worker/jobs.sqlite?mode=ro',uri=True) as db:
        return json.loads(db.execute('SELECT body FROM jobs ORDER BY created DESC LIMIT 1').fetchone()[0])
deadline=time.monotonic()+1800
while 'UPGRADE AND RECOVERY SMOKE PASS' not in Path('/root/nas-v021-upgrade-test.log').read_text():
    status=run(['systemctl','show','nas-v021-upgrade-test','-p','ActiveState','--value']).strip()
    if status in ('failed','inactive') or time.monotonic()>deadline:raise SystemExit('Prior upgrade/recovery test did not pass')
    time.sleep(5)
before=saved_job()
if before['status']!='succeeded':raise SystemExit('Verified completed job required')
conf=Path('/etc/nas-setup');config_before={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in conf.iterdir() if f.is_file()}
archive=Path(a.archive)
if hashlib.sha256(archive.read_bytes()).hexdigest()!=a.sha256:raise SystemExit('Archive checksum mismatch')
with tempfile.TemporaryDirectory(prefix='nas-final-',dir='/root') as tmp:
    with tarfile.open(archive) as bundle:bundle.extractall(tmp,filter='data')
    update=subprocess.run(['bash',tmp+'/nas-setup-0.2.1/scripts/bootstrap.sh','--update'],capture_output=True,text=True,timeout=1200)
    fd=os.open('/root/nas-final-update-private.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(update.stdout+'\n'+update.stderr)
    if update.returncode:raise SystemExit('Final update failed; inspect private guest log')
if saved_job()!=before:raise AssertionError('Final update changed saved job')
if config_before!={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in conf.iterdir() if f.is_file()}:raise AssertionError('Configuration changed')
origin=json.loads((conf/'config.json').read_text())['origin']
context=ssl.create_default_context(cafile=str(conf/'server.crt'))
http=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
code=re.search(r'Código / Code \(15 min\): ([^\s]+)',update.stdout).group(1)
req=urllib.request.Request(origin+'/api/login',data=json.dumps({'code':code}).encode(),headers={'Origin':origin,'Content-Type':'application/json'})
with http.open(req,timeout=30) as response:auth=json.loads(response.read())
with http.open(origin+'/api/job',timeout=60) as response:job=json.loads(response.read())
if job['id']!=before['id'] or job['status']!='succeeded':raise AssertionError('HTTPS job state changed')
print('PASS final runtime patch update: unchanged completed job, configuration/certificate, verified HTTPS login and healthy RAID1',flush=True)
# Exercise the narrow automatic repair with an explicit pending marker. This
# deploys mdadm configuration only; it does not create/format/change any disk.
xml=Path('/etc/openmediavault/config.xml');digest=hashlib.sha256(xml.read_bytes()).hexdigest()
run(['/bin/sh','-c','. /usr/share/openmediavault/scripts/helper-functions; omv_module_set_dirty mdadm'])
run(['/opt/nas-setup/venv/bin/python','-c',
     "from nas_setup.omv import OmvProvisioner; from nas_setup.system import System; a=OmvProvisioner(System()); assert a._rpc('config_dirty_modules')==['mdadm']; a._settle_prerequisites(True); assert not a._rpc('config_dirty',{'modules':[]})"],timeout=600)
if hashlib.sha256(xml.read_bytes()).hexdigest()!=digest:raise AssertionError('Narrow repair changed approved XML')
with http.open(origin+'/api/job',timeout=60) as response:job=json.loads(response.read())
if job['id']!=before['id'] or job['status']!='succeeded' or job['resources']['raid']['state']!='healthy':raise AssertionError('Repair changed verified array')
print('PASS automatic mdadm-only repair clears pending marker and preserves verified array/configuration',flush=True)
print('FINAL PATCH RUNTIME PASS '+a.sha256,flush=True)
