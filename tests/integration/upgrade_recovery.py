"""Run only inside the dedicated disposable NAS VM. Never target physical storage."""
import argparse, hashlib, http.cookiejar, json, os, platform, pwd, re, secrets
import socket, ssl, stat, subprocess, tarfile, time, urllib.error, urllib.request
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('archive');p.add_argument('sha256');p.add_argument('update_archive');p.add_argument('update_sha256')
a=p.parse_args();a.enabled=True
if os.geteuid()!=0 or platform.system()!='Linux' or socket.gethostname()!='nas-v02-vm':
    raise SystemExit('Disposable VM root environment required')
marker=Path('/root/NAS_SETUP_DISPOSABLE_VM')
if not marker.is_file() or marker.is_symlink() or marker.stat().st_uid!=0:
    raise SystemExit('Disposable marker missing')
def command(args,timeout=60):
    return subprocess.run(args,capture_output=True,text=True,check=True,timeout=timeout).stdout
block=json.loads(command(['lsblk','--json','--bytes','--output','NAME,TYPE,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS,FSTYPE,PTTYPE']))['blockdevices']
disks=[d for d in block if d['type']=='disk']
expected={'NASV02SYSTEM'}|{'NASV02DATA'+str(i) for i in range(1,5)}
if len(disks)!=5 or {d['serial'] for d in disks}!=expected:
    raise SystemExit('Unexpected physical/virtual disks; refusing smoke test')
for disk in disks:
    if disk['serial'].startswith('NASV02DATA'):
        if disk['model'].strip()!='QEMU HARDDISK' or disk['tran']!='sata' or disk['size']!=16*1024**3 or disk.get('children') or disk.get('fstype') or disk.get('pttype') or any(disk.get('mountpoints') or []):
            raise SystemExit('Requires untouched synthetic SATA images')
# MD may already be installed; pending state is injected explicitly below.
archive=Path(a.archive)
if hashlib.sha256(archive.read_bytes()).hexdigest()!=a.sha256:
    raise SystemExit('Archive checksum mismatch')
work=Path('/root/nas-upgrade-smoke');work.mkdir(mode=0o700)
with tarfile.open(archive) as bundle:bundle.extractall(work,filter='data')
source=work/'nas-setup-0.2.0'
boot=subprocess.run(['bash',str(source/'scripts/bootstrap.sh'),'--address','10.0.2.15','--port','8443'],capture_output=True,text=True,timeout=1200)
private=work/'bootstrap-private.log'
fd=os.open(private,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:f.write(boot.stdout+'\n'+boot.stderr)
if boot.returncode:raise SystemExit('Bootstrap failed; inspect private guest log')
match=re.search(r'Código / Code \(15 min\): ([^\s]+)',boot.stdout)
if not match:raise SystemExit('Bootstrap did not return a pairing code')
code=match.group(1)
origin='https://10.0.2.15:8443'
context=ssl.create_default_context(cafile='/etc/nas-setup/server.crt')
jar=http.cookiejar.CookieJar()
def client():return urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context),urllib.request.HTTPCookieProcessor(jar))
http=client();csrf=None

def api(path,body=None,status=200,include_csrf=True):
    headers={'Origin':origin}
    if csrf and include_csrf:headers['X-CSRF-Token']=csrf
    if body is not None:headers['Content-Type']='application/json'
    req=urllib.request.Request(origin+'/api/'+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
    try:
        with http.open(req,timeout=60) as response:result=(response.status,response.read())
    except urllib.error.HTTPError as error:result=(error.code,error.read())
    if result[0]!=status:raise AssertionError('API '+path+' returned '+str(result[0])+' expected '+str(status))
    return json.loads(result[1])

def wait_web():
    deadline=time.monotonic()+90
    while True:
        try:api('session');return
        except (urllib.error.URLError,ConnectionError):
            if time.monotonic()>deadline:raise
            time.sleep(1)
wait_web()
for service in ('nas-setup-worker','nas-setup-web'):
    if command(['systemctl','is-active',service]).strip()!='active':raise AssertionError(service)
user=pwd.getpwnam('nas-setup')
for name,owner in [('worker',0),('web',user.pw_uid)]:
    info=Path('/var/lib/nas-setup',name).stat()
    if info.st_uid!=owner or stat.S_IMODE(info.st_mode)!=0o700:raise AssertionError('State ownership/mode')
key=Path('/etc/nas-setup/server.key').stat()
if key.st_uid!=0 or key.st_gid!=user.pw_gid or stat.S_IMODE(key.st_mode)!=0o640:raise AssertionError('TLS key access')
with socket.create_connection(('10.0.2.15',8443),timeout=10) as raw:
    with context.wrap_socket(raw,server_hostname='10.0.2.15') as secure:
        digest=hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest().upper()
fingerprint=':'.join(digest[i:i+2] for i in range(0,len(digest),2))
if fingerprint not in boot.stdout:raise AssertionError('Printed/server TLS certificate mismatch')
print('PASS packaged bootstrap, systemd services, private state, verified HTTPS fingerprint',flush=True)
# Fault injection only in this identity-checked disposable VM. The clean old
# release did not reproduce the user's unknown pending-change origin. Insert
# real OMV pending markers at the reported boundary, leaving storage untouched.
command(['systemctl','stop','nas-setup-web','nas-setup-worker'])
module=Path(command(['/opt/nas-setup/venv/bin/python','-c','import nas_setup.omv; print(nas_setup.omv.__file__)']).strip())
original=module.read_text()
needle="        if raid:self._rpc('md_candidates')\n        self._config_digest=self._digest()"
hook="\n        self.system.run(['/bin/sh','-c','. /usr/share/openmediavault/scripts/helper-functions; omv_module_set_dirty mdadm; omv_module_set_dirty fstab'])"
if original.count(needle)!=1:raise AssertionError('Unexpected old-release hook boundary')
module.write_text(original.replace(needle,needle+hook))
command(['systemctl','start','nas-setup-worker','nas-setup-web']);wait_web()
print('INJECTED real pending mdadm/fstab at the old prerequisite/storage boundary; origin is test-controlled',flush=True)
if api('inventory',status=401).get('error')!='login_required':raise AssertionError('Anonymous inventory access')
csrf=api('login',{'code':code})['csrf'];code=None
inv=api('inventory')
if not inv['supported'] or inv['demo'] or inv['provisioning_enabled']!=a.enabled:raise AssertionError('Wrong live/gate inventory')
if len(inv['disks'])!=5:raise AssertionError('Inventory disk count')
if next(d for d in inv['disks'] if d['serial']=='NASV02SYSTEM')['eligible']:raise AssertionError('System disk offered')
targets=[next(d for d in inv['disks'] if d['serial']=='NASV02DATA'+str(i)) for i in (1,2)]
if not all(d['eligible'] for d in targets):raise AssertionError('Synthetic data disk unavailable')
target_ids=[d['id'] for d in targets]
selection={'storage':{'mode':'raid1','disks':target_ids},'users':[{'name':'smokeana'},{'name':'smokebob'}],
           'shares':[{'name':'SmokeFiles','folder':'files','permissions':{'smokeana':'rw','smokebob':'ro'}}],'apps':[]}
plan=api('setup/plan',selection)
if not plan['destructive'] or not plan['confirmation_required']:raise AssertionError('Missing erasure consent')
passwords={name:secrets.token_urlsafe(24) for name in ('smokeana','smokebob')}
body={'plan_id':plan['plan_id'],'confirmation':'ERASE','erase_disks':target_ids,'passwords':passwords}
if api('setup/start',body,status=403,include_csrf=False).get('error')!='csrf_invalid':raise AssertionError('CSRF accepted')
if api('setup/start',{'plan_id':plan['plan_id'],'passwords':passwords},status=409).get('error')!='confirmation_required':raise AssertionError('Missing consent accepted')
print('PASS real HTTPS login, root-worker inventory/plan, CSRF and erasure boundaries',flush=True)
if not a.enabled:
    if api('setup/start',body,status=409).get('error')!='provisioning_unvalidated':raise AssertionError('Disabled gate accepted')
    if api('job') is not None:raise AssertionError('Gated request created job')
    print('PASS disabled production gate; no provisioning dispatched',flush=True)
else:
    job=api('setup/start',body)
    # The injected pending configuration must reproduce the reported failure shape.
    original_id=job['id'];deadline=time.monotonic()+900
    while job['status']=='running':
        if time.monotonic()>deadline:raise TimeoutError('Old job did not stop')
        time.sleep(3);job=api('job')
    if job['status']!='failed' or job['error']!='omv_configuration_changed':
        raise AssertionError('Old release did not reproduce injected pending-config failure: '+str(job.get('error')))
    import sqlite3
    journal=Path('/var/lib/nas-setup/worker/setup')/job['id']/'journal'
    with sqlite3.connect('file:'+str(journal/'operations.sqlite')+'?mode=ro',uri=True) as db:
        if db.execute('SELECT count(*) FROM operations').fetchone()[0]:raise AssertionError('Old job mutated storage')
    if (journal/'omv-config-checkpoint.json').exists():raise AssertionError('Unexpected checkpoint')
    print('PASS reproduced injected v0.2.0 pending-config failure, empty operation journal and absent checkpoint',flush=True)
    before_config={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in Path('/etc/nas-setup').iterdir() if path.is_file()}
    update_archive=Path(a.update_archive)
    if hashlib.sha256(update_archive.read_bytes()).hexdigest()!=a.update_sha256:raise AssertionError('Update checksum mismatch')
    with tarfile.open(update_archive) as bundle:bundle.extractall(work,filter='data')
    update=subprocess.run(['bash',str(work/'nas-setup-0.2.1/scripts/bootstrap.sh'),'--update'],capture_output=True,text=True,timeout=1200)
    fd=os.open(work/'update-private.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(update.stdout+'\n'+update.stderr)
    if update.returncode:raise AssertionError('Update failed; inspect private guest log')
    if before_config!={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in Path('/etc/nas-setup').iterdir() if path.is_file()}:raise AssertionError('Update changed certificate/config')
    match=re.search(r'Código / Code \(15 min\): ([^\s]+)',update.stdout)
    if not match:raise AssertionError('Update pairing code missing')
    jar.clear();csrf=None;wait_web();csrf=api('login',{'code':match.group(1)})['csrf']
    job=api('job')
    if job['id']!=original_id or job['status']!='failed':raise AssertionError('Update lost failed job')
    print('PASS update retains failed job and HTTPS/config; fresh login successful',flush=True)
    offer=api('setup/recovery',{})
    if offer['modules']!=['fstab','mdadm'] or not offer['pending_id']:raise AssertionError('Pending approval does not match injected configuration')
    job=api('setup/resume',{'passwords':passwords,'pending_id':offer['pending_id']})
    if job['id']!=original_id:raise AssertionError('Retry created another job')
    print('PASS retry accepted original disk plan plus explicit pending-config approval, no new ERASE/plan request',flush=True)
    deadline=time.monotonic()+1200;previous=None
    while True:
        job=api('job')
        if any(secret in json.dumps(job) for secret in passwords.values()):raise AssertionError('Password in job response')
        running=next((s['id'] for s in job['steps'] if s['status']=='running'),job['status'])
        if running!=previous:print('LIVE WIZARD STEP',running,flush=True);previous=running
        if job['status']=='succeeded':break
        if job['status'] not in ('running','waiting-for-sync'):raise AssertionError('Wizard stopped: '+str(job.get('error')))
        if time.monotonic()>deadline:raise TimeoutError('Packaged setup timed out')
        time.sleep(3)
    if job['resources']['raid']!={'state':'healthy','progress':100,'tolerance':1}:raise AssertionError('Mirror not fully synchronized')
    if command(['dpkg-query','--show','--showformat=${db:Status-Status}\t${Version}','openmediavault-md']).strip()!='installed\t8.1.6-1':raise AssertionError('Wizard did not install supported MD plugin')
    if job['resources']['shares'][0]['permissions']!={'smokeana':'rw','smokebob':'ro'}:raise AssertionError('Permission contract changed')
    if list(Path('/var/lib/nas-setup/worker/setup').glob('*/credentials.json')):raise AssertionError('Credentials not removed')
    print('PASS upgraded HTTPS wizard completes healthy RAID1/users/share, no password in job history',flush=True)
    api('finish',{})
    deadline=time.monotonic()+30
    while any(subprocess.run(['systemctl','is-active','--quiet',s]).returncode==0 for s in ('nas-setup-web','nas-setup-worker')):
        if time.monotonic()>deadline:raise TimeoutError('Finish did not stop setup services')
        time.sleep(1)
    if command(['systemctl','is-active','smbd']).strip()!='active':raise AssertionError('Finish stopped SMB')
    reopened=command(['/usr/local/bin/nas-setup','reopen'])
    match=re.search(r'Código / Code \(15 min\): ([^\s]+)',reopened)
    if not match:raise AssertionError('Reopen code missing')
    jar.clear();csrf=None;wait_web();csrf=api('login',{'code':match.group(1)})['csrf']
    if api('job')['status']!='succeeded':raise AssertionError('Reopen lost verified setup')
    print('PASS Finish stops only installer; SMB continues; reopen authenticates and preserves setup',flush=True)
print('UPGRADE AND RECOVERY SMOKE PASS',flush=True)
