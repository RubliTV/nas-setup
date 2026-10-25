"""Lost-response tests exercise actual adapter stage closures, not just journal APIs."""
from types import SimpleNamespace
import pytest
from nas_setup.journal import OperationJournal
from nas_setup.models import SetupError
from nas_setup.omv import OmvProvisioner


@pytest.mark.parametrize('stage',['wipe','array','mount','user','share'])
def test_uncertain_stage_never_repeats_remote_mutation(tmp_path,stage):
    calls=[]
    adapter=OmvProvisioner(SimpleNamespace(state=tmp_path,
        run=lambda *a,**k:SimpleNamespace(returncode=2,stdout='')))
    journal=OperationJournal(tmp_path/'journal')
    def lost_reply(operation,params):
        calls.append(operation)
        raise RuntimeError('simulated loss after remote mutation')
    adapter._mutate=lost_reply
    adapter._check_target=lambda *a,**k:'/dev/disk/by-id/ata-data'
    adapter._db=lambda *a:[]
    targets=[{'id':'/dev/disk/by-id/ata-data'+str(i),'serial':'DATA'+str(i),'transport':'sata'} for i in (1,2)]
    if stage=='array':
        for target in targets:
            key='wipe.'+target['serial']
            journal.perform(key+'.dispatch',lambda:{'handle':'/tmp/owned'},destructive=True)
            journal.perform(key+'.result',lambda:{'id':target['id']})
    if stage in ('wipe','array'):
        invoke=lambda:adapter._raid('raid1',targets,'owner',journal)
        expected='disk_wipe' if stage=='wipe' else 'md_create'
    elif stage=='mount':
        invoke=lambda:adapter._mount_new({'uuid':'created-filesystem'},'owner',journal)
        expected='mount_set'
    elif stage=='user':
        invoke=lambda:adapter._user('ana','disposable-password','owner',journal)
        expected='user_set'
    else:
        invoke=lambda:adapter._share({'name':'Files','folder':'files','permissions':{'ana':'rw'}},
            {'id':'filesystem','path':str(tmp_path),'mount_uuid':'mount'},'owner',journal)
        expected='share_set'
    with pytest.raises(RuntimeError,match='simulated loss'):invoke()
    # A new process opens the durable journal after the response was lost.
    journal=OperationJournal(tmp_path/'journal')
    with pytest.raises(SetupError,match='recovery_required'):invoke()
    assert calls==[expected]


def test_lost_mount_apply_reply_reconciles_only_verified_mounted_result(tmp_path):
    calls=[]
    adapter=OmvProvisioner(SimpleNamespace(state=tmp_path))
    journal=OperationJournal(tmp_path/'journal')
    mount={'uuid':'registered-mount','dir':'/srv/owned','fsname':'/dev/disk/by-uuid/fs','type':'ext4','comment':'nas-setup-owner'}
    adapter._db=lambda model:[mount]
    def mutate(operation,params):
        calls.append(operation)
        if operation=='mount_set':return mount
        raise RuntimeError('deployment completed but reply lost')
    adapter._mutate=mutate
    with pytest.raises(RuntimeError):adapter._mount_new({'uuid':'fs'},'owner',journal)
    def missing(identity):raise SetupError('volume_missing')
    adapter._mounted=missing
    with pytest.raises(SetupError,match='volume_missing'):
        adapter._mount_new({'uuid':'fs'},'owner',OperationJournal(tmp_path/'journal'))
    adapter._mounted=lambda identity:{'id':'fs','path':'/srv/owned','mount_uuid':'registered-mount'}
    assert adapter._mount_new({'uuid':'fs'},'owner',OperationJournal(tmp_path/'journal'))['id']=='fs'
    assert calls==['mount_set','config_apply']
