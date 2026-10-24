"""Disposable VM ONLY. Hard-coded synthetic disk serial allowlist; never production."""
import json, os, platform, socket, subprocess, sys, time
from pathlib import Path
from nas_setup.system import System
from nas_setup.omv import OmvProvisioner
from nas_setup.setup_models import SetupSelection, plan_setup
from nas_setup.journal import OperationJournal

if os.geteuid()!=0 or platform.system()!='Linux' or socket.gethostname()!='nas-v02-vm':
    raise SystemExit('Run only as root in the dedicated nas-v02-vm guest.')
marker=Path('/root/NAS_SETUP_DISPOSABLE_VM')
if not marker.is_file() or marker.is_symlink() or marker.stat().st_uid!=0:
    raise SystemExit('Root-owned disposable VM marker required.')
MODE=sys.argv[1] if len(sys.argv)==2 else ''
if MODE not in ('single','raid1','raid5','raid6','verify','lost-format','degraded','conflicts','existing'):raise SystemExit('mode rejected')
STATE=Path('/root/nas-v02-exercise')
STATE.mkdir(mode=0o700,exist_ok=True)
class DisposableAdapter(OmvProvisioner):
    def _require_validated(self):
        if not Path('/root/NAS_SETUP_DISPOSABLE_VM').exists():raise RuntimeError('VM marker required')
    def _mutate(self,op,params):
        print('MUTATION',op,flush=True)
        result=super()._mutate(op,params)
        if MODE=='lost-format' and op=='filesystem_create':raise RuntimeError('injected lost response')
        return result
system=System(STATE/'worker');adapter=DisposableAdapter(system)
# Refuse any unexpected physical devices, even in this explicit VM harness.
disks=adapter.inventory()['disks']
expected_serials={'NASV02SYSTEM'}|{'NASV02DATA'+str(i) for i in range(1,5)}
if len(disks)!=5 or {d['serial'] for d in disks}!=expected_serials:
    raise SystemExit('Unexpected disks: '+repr(disks))
if next(d for d in disks if d['serial']=='NASV02SYSTEM')['eligible']:
    raise SystemExit('OS disk must be excluded.')
if not all(d['model']=='QEMU HARDDISK' and d['transport']=='sata' and d['size_bytes']==16*1024**3
           for d in disks if d['serial'].startswith('NASV02DATA')):
    raise SystemExit('Data disks must be exact disposable QEMU SATA images.')

def smb(user,share,command,success=True):
    auth=STATE/('smb-'+user+'.auth')
    if not auth.exists():
        fd=os.open(auth,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write('username = '+user+'\npassword = '+passwords[user]+'\n')
    result=subprocess.run(['smbclient','//127.0.0.1/'+share,'-A',str(auth),'-c',command],capture_output=True,text=True)
    print('SMB',user,share,'success' if result.returncode==0 else 'denied',flush=True)
    if bool(result.returncode==0)!=success:raise AssertionError(result.stdout+'\n'+result.stderr)

if MODE=='verify':
    resources=json.loads((STATE/'resources.json').read_text())
    status=adapter.status(resources)
    assert status['state']=='healthy',status
    print('REBOOT STATUS',json.dumps(status),flush=True)
    passwords={}
    smb('nasana','Files','get test.txt '+str(STATE/'download-after-reboot'))
    assert (STATE/'download-after-reboot').read_text()=='disposable NAS ACL verification\n'
    smb('naseve','Files','ls',False)
    raise SystemExit(0)

if MODE=='existing':
    resources=json.loads((STATE/'resources.json').read_text())
    selection=SetupSelection(storage={'mode':'existing','volume':resources['volume']},apps=[],timezone='UTC')
    inventory={**system.inventory(),**adapter.inventory(),'provisioning_enabled':True}
    plan=plan_setup(selection,inventory)
    def forbidden_mutation(op,params):raise AssertionError('existing volume dispatched mutation '+op)
    adapter._mutate=forbidden_mutation
    adapter.prepare(selection.model_dump(),plan)
    reused=adapter.provision(selection.model_dump(),plan,OperationJournal(STATE/'existing-journal'),{})
    assert reused['volume']==resources['volume'] and reused['mount_path']==resources['mount_path']
    print('VERIFIED EXISTING VOLUME ZERO MUTATION RPCS',flush=True)
    raise SystemExit(0)

if MODE=='degraded':
    resources=json.loads((STATE/'resources.json').read_text())
    if not resources.get('md') or adapter.status(resources)['state']!='healthy':
        raise SystemExit('Healthy owned disposable RAID required before failure injection.')
    target=resources['targets'][-1]
    if target['serial'] not in {'NASV02DATA1','NASV02DATA2','NASV02DATA3','NASV02DATA4'}:
        raise SystemExit('Refusing non-disposable RAID member.')
    md=resources['md']['devicefile'];member=target['id']
    subprocess.run(['mdadm','--manage',md,'--fail',member],check=True)
    subprocess.run(['mdadm','--manage',md,'--remove',member],check=True)
    status=adapter.status(resources)
    assert status['state']=='degraded',status
    print('VERIFIED DEGRADED MEMBER DETECTION',json.dumps(status),flush=True)
    raise SystemExit(0)

if MODE=='conflicts':
    from nas_setup.models import SetupError
    from nas_setup.omv import SMB_SETTINGS_KEYS
    resources=json.loads((STATE/'resources.json').read_text())
    assert adapter.status(resources)['state']=='healthy'
    inventory={**system.inventory(),**adapter.inventory(),'provisioning_enabled':True}
    spare=next(d for d in inventory['disks'] if d['eligible'] and d['serial'].startswith('NASV02DATA'))
    calls=[]
    def forbidden_mutation(op,params):
        calls.append(op);raise AssertionError('conflict preflight dispatched '+op)
    adapter._mutate=forbidden_mutation
    def check_conflict(users,shares,code):
        selection=SetupSelection(storage={'mode':'single','disks':[spare['id']]},users=users,shares=shares,apps=[],timezone='UTC')
        plan=plan_setup(selection,inventory)
        try:adapter.prepare(selection.model_dump(),plan)
        except SetupError as exc:assert exc.code==code,(code,exc.code)
        else:raise AssertionError('expected preflight conflict '+code)
        assert not calls,calls
        print('VERIFIED PREFLIGHT CONFLICT ZERO MUTATIONS',code,flush=True)
    check_conflict([{'name':'nasana'}],[],'existing_installation')
    check_conflict([{'name':'nasnew'}],[{'name':'Files','folder':'files','permissions':{'nasnew':'rw'}}],'existing_installation')
    original=adapter._rpc('smb_settings_get')
    original={k:original[k] for k in SMB_SETTINGS_KEYS}
    pending=dict(original,serverstring=original['serverstring']+' pending-test')
    adapter._rpc('smb_settings_set',pending)
    try:check_conflict([],[],'omv_configuration_changed')
    finally:
        adapter._rpc('smb_settings_set',original)
        adapter._rpc('config_apply',{'modules':['samba'],'force':False})
    print('CONFLICT MODE PASS',flush=True)
    raise SystemExit(0)

count={'single':1,'raid1':2,'raid5':3,'raid6':4,'lost-format':1}[MODE]
ids=[d['id'] for d in sorted(disks,key=lambda d:d['serial']) if d['serial'].startswith('NASV02DATA')][:count]
assert len(ids)==count
selection=SetupSelection(storage={'mode':'single' if MODE=='lost-format' else MODE,'disks':ids},
    users=[{'name':u} for u in ('nasana','nasbob','naseve')],
    shares=[{'name':'Files','folder':'files','permissions':{'nasana':'rw','nasbob':'ro','naseve':'none'}},
            {'name':'Media','folder':'media','permissions':{'nasana':'none','nasbob':'rw','naseve':'ro'}}],apps=[],timezone='UTC')
inventory={**system.inventory(),**adapter.inventory(),'provisioning_enabled':True}
plan=plan_setup(selection,inventory)
print('TARGETS',[(d['serial'],d['id']) for d in plan['targets']],flush=True)
passwords={u['name']:'Disposable-'+os.urandom(16).hex() for u in selection.model_dump()['users']}
adapter.prepare(selection.model_dump(),plan)
journal=OperationJournal(STATE/'journal')
try:
    resources=adapter.provision(selection.model_dump(),plan,journal,passwords)
except RuntimeError:
    if MODE!='lost-format':raise
    print('EXPECTED LOST FORMAT RESPONSE',flush=True)
    from nas_setup.models import SetupError
    try:adapter.provision(selection.model_dump(),plan,journal,passwords)
    except SetupError as exc:
        assert exc.code=='recovery_required',exc.code
        print('VERIFIED NO REPEATED FORMAT',flush=True)
        raise SystemExit(0)
    raise AssertionError('lost response unexpectedly continued')
print('RESOURCES',json.dumps(resources),flush=True)
(STATE/'resources.json').write_text(json.dumps(resources));(STATE/'resources.json').chmod(0o600)
(STATE/'plan.json').write_text(json.dumps(plan));(STATE/'plan.json').chmod(0o600)
# Resuming a completed resource journal must invoke zero mutation RPCs.
def no_mutations(*args,**kwargs):raise AssertionError('resume attempted mutation '+args[0])
adapter._mutate=no_mutations
again=adapter.provision(selection.model_dump(),plan,journal,passwords)
assert again['volume']==resources['volume']
print('VERIFIED COMPLETED JOURNAL NO MUTATIONS',flush=True)
payload=STATE/'payload.txt';payload.write_text('disposable NAS ACL verification\n')
smb('nasana','Files','put '+str(payload)+' test.txt')
smb('nasbob','Files','get test.txt '+str(STATE/'download'))
assert (STATE/'download').read_bytes()==payload.read_bytes()
smb('nasbob','Files','put '+str(payload)+' forbidden.txt',False)
smb('naseve','Files','ls',False)
smb('nasbob','Media','put '+str(payload)+' media.txt')
smb('naseve','Media','get media.txt '+str(STATE/'media-download'))
assert (STATE/'media-download').read_bytes()==payload.read_bytes()
smb('naseve','Media','put '+str(payload)+' forbidden.txt',False)
smb('nasana','Media','ls',False)
for share,user in [('Files','naseve'),('Media','nasana')]:
    path=next(s['path'] for s in resources['shares'] if s['name']==share)
    result=subprocess.run(['runuser','-u',user,'--','ls',path],capture_output=True)
    assert result.returncode!=0
print('VERIFIED POSIX CROSS-USER DENIALS',flush=True)
end=time.monotonic()+600
while True:
    status=adapter.status(resources)
    print('RAID STATUS',json.dumps(status),flush=True)
    if status['state']=='healthy':break
    assert status['state']=='syncing'
    if time.monotonic()>end:raise TimeoutError('array did not synchronize within test budget')
    time.sleep(15)
print('MODE PASS',MODE,flush=True)
