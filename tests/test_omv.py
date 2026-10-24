"""Safety contracts for the disabled-until-validated OMV adapter."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from nas_setup.models import SetupError
from nas_setup import omv


def disk(name, **kw):
    return dict(name='/dev/'+name, kname=name, type='disk', size=1000000,
                serial=name+'-serial', wwn='', tran='sata', ro=False, rm=False,
                mountpoints=[], children=[], **kw)


def topology(*extra):
    osdisk=disk('sda');osdisk['children']=[{'name':'/dev/sda1','type':'part','children':[
        {'name':'/dev/md0','type':'raid1','children':[{'name':'/dev/dm-0','type':'lvm','mountpoints':['/']}]}]}]
    data=disk('sdb')
    nodes=[osdisk,data,*extra]
    ids={n['name']:['/dev/disk/by-id/ata-'+n['serial']] for n in nodes}
    return {'blockdevices':nodes},ids


def test_system_ancestors_through_md_lvm_never_selectable():
    tree,ids=topology()
    found=omv.classify_disks(tree,ids,{})
    assert [(d['eligible'],d['reason']) for d in found]==[(False,'system_disk'),(True,None)]


def test_mounted_partition_holders_unknown_transport_and_duplicate_identity():
    tree,ids=topology(disk('sdc'),disk('sdd'),disk('sde'))
    tree['blockdevices'][1]['children']=[{'name':'/dev/sdb1','type':'part','mountpoints':['/srv/data']}]
    tree['blockdevices'][3]['tran']=None
    ids['/dev/sde']=ids['/dev/sdb']
    out={d['device']:d for d in omv.classify_disks(tree,ids,{'/dev/sdc':['dm-3']})}
    assert not any(out['/dev/'+n]['eligible'] for n in ['sdb','sdc','sdd','sde'])
    assert out['/dev/sdc']['reason']=='active_holder'
    assert out['/dev/sdd']['reason']=='unknown_transport'


def test_missing_root_topology_fails_closed():
    tree,ids=topology();tree['blockdevices']=tree['blockdevices'][1:]
    assert not omv.classify_disks(tree,ids,{})[0]['eligible']


def test_unvalidated_adapter_refuses_even_existing_volume_and_no_shell_calls(tmp_path,monkeypatch):
    monkeypatch.setattr(omv,'ADAPTER_VALIDATED',False)
    class System:
        state=tmp_path
        def run(self,*a,**kw):pytest.fail('must gate before subprocess')
    adapter=omv.OmvProvisioner(System())
    with pytest.raises(SetupError,match='provisioning_unvalidated'):
        adapter.prepare({'storage':{'mode':'existing','volume':'data'}},{})


def test_privilege_matrix_uses_named_user_denials_and_no_group_fallback():
    privileges,acl=omv.share_permissions('uuid',{'ana':'rw','bob':'ro','eve':'none'})
    assert privileges=={'uuid':'uuid','privileges':[
        {'name':'ana','perms':7,'type':'user'}, {'name':'bob','perms':5,'type':'user'},
        {'name':'eve','perms':0,'type':'user'}]}
    assert acl['owner']=='root' and acl['group']=='root'
    assert acl['groupperms']==acl['otherperms']==0
    assert acl['users']==[{'name':'ana','perms':7},{'name':'bob','perms':5},{'name':'eve','perms':0}]
    assert acl['recursive'] is False


def test_rpc_password_only_stdin_and_error_redacted(monkeypatch,tmp_path):
    seen=[]
    def run(argv,**kwargs):
        seen.append((argv,kwargs))
        return SimpleNamespace(returncode=1,stdout='',stderr='password=supersecret')
    monkeypatch.setattr(omv.subprocess,'run',run)
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    with pytest.raises(SetupError,match='omv_rpc_failed') as exc:
        adapter._rpc('user_set',{'password':'supersecret'})
    assert 'supersecret' not in str(exc.value)
    assert 'supersecret' not in repr(seen[0][0])
    assert json.loads(seen[0][1]['input'])['params']['password']=='supersecret'


def test_bridge_is_fixed_allowlist():
    src=(Path(omv.__file__).parent/'omv_bridge.php').read_text()
    assert 'stream_get_contents(STDIN' in src
    assert "'user_set'" in src and "'filesystem_create'" in src
    assert "['service']" not in src and "['method']" not in src


def test_raid_status_does_not_call_degraded_protected():
    assert omv.raid_health('idle','0', 'none', 1)=={'state':'healthy','progress':100,'tolerance':1}
    assert omv.raid_health('resync','0','25 / 100',1)=={'state':'syncing','progress':25,'tolerance':1}
    assert omv.raid_health('idle','1','none',2)['state']=='degraded'
    assert omv.raid_health('unknown','0','none',1)['state']=='degraded'


def test_lost_format_response_never_dispatches_format_again(tmp_path):
    from nas_setup.journal import OperationJournal
    calls=[]
    class Adapter(omv.OmvProvisioner):
        def _mutate(self,operation,params):
            calls.append(operation)
            raise RuntimeError('power loss after OMV received format')
    adapter=Adapter(SimpleNamespace(state=tmp_path));journal=OperationJournal(tmp_path/'journal')
    adapter._filesystem_uuids=lambda target:[]
    with pytest.raises(RuntimeError):adapter._format('/dev/disk/by-id/ata-data',journal,lambda:None)
    with pytest.raises(SetupError,match='recovery_required'):
        adapter._format('/dev/disk/by-id/ata-data',journal,lambda:None)
    assert calls==['filesystem_create']


def test_completed_format_is_verified_without_another_dispatch(tmp_path):
    from nas_setup.journal import OperationJournal
    calls=[]
    class Adapter(omv.OmvProvisioner):
        def _mutate(self,operation,params):calls.append(operation);return '/tmp/bgstatusABC'
        def _wait_bg(self,handle):assert handle=='/tmp/bgstatusABC'
        def _filesystem_for(self,target):return {'uuid':'filesystem-uuid','devicefile':'/dev/sdb1','type':'ext4'}
    adapter=Adapter(SimpleNamespace(state=tmp_path));journal=OperationJournal(tmp_path/'journal')
    adapter._filesystem_uuids=lambda target:[]
    result=adapter._format('/dev/disk/by-id/ata-data',journal,lambda:None)
    assert result['uuid']=='filesystem-uuid'
    assert adapter._format('/dev/disk/by-id/ata-data',journal,lambda:None)==result
    assert calls==['filesystem_create']
    adapter._filesystem_for=lambda target:{'uuid':'substituted-uuid','devicefile':'/dev/sdb1','type':'ext4'}
    with pytest.raises(SetupError,match='recovery_required'):
        adapter._format('/dev/disk/by-id/ata-data',journal,lambda:None)


def test_same_shared_folder_path_cannot_adopt_unmanaged_directory(tmp_path):
    (tmp_path/'media').mkdir()
    with pytest.raises(SetupError,match='existing_installation'):
        omv.safe_share_path(tmp_path,'media',must_be_new=True)
    (tmp_path/'escape').symlink_to('/tmp',target_is_directory=True)
    with pytest.raises(SetupError,match='unsafe_storage'):
        omv.safe_share_path(tmp_path,'escape')
    for value in ('../outside','/tmp/owned','.nas-setup',''):
        with pytest.raises(SetupError,match='unsafe_storage'):omv.safe_share_path(tmp_path,value)


def test_schema_changed_host_refuses_before_any_mutation(tmp_path):
    (tmp_path/'rpc.diskmgmt.json').write_text('{}')
    with pytest.raises(SetupError,match='omv_schema_unsupported'):
        omv.verify_schemas(tmp_path,raid=False)


def test_disk_identity_and_signature_change_rejected_at_destructive_boundary(tmp_path):
    tree,ids=topology();disks=omv.classify_disks(tree,ids,{})
    original=dict(disks[1]);adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    adapter._discover=lambda:(tree,disks)
    assert adapter._check_target(original)==original['id']
    disks[1]['serial']='replacement'
    with pytest.raises(SetupError,match='storage_changed'):adapter._check_target(original)
    disks[1]['serial']=original['serial'];disks[1]['signatures']=['ext4:external']
    with pytest.raises(SetupError,match='storage_changed'):adapter._check_target(original)
    with pytest.raises(SetupError,match='storage_changed'):adapter._check_target(original,blank=True)
    disks[1]['signatures']=[]
    assert adapter._check_target(original,blank=True)==original['id']


def test_user_creation_never_resets_existing_unmanaged_user(tmp_path):
    from nas_setup.journal import OperationJournal
    class System:
        state=tmp_path
        def run(self,args,**kw):
            assert args==['getent','passwd','ana']
            return SimpleNamespace(returncode=0,stdout='ana:x:1000:100::/home/ana:/bin/bash\n')
    adapter=omv.OmvProvisioner(System())
    adapter._mutate=lambda *args:pytest.fail('must not reset unmanaged user')
    with pytest.raises(SetupError,match='existing_installation'):
        adapter._user('ana','test-password','owner',OperationJournal(tmp_path/'journal'))


def test_owned_user_with_elevated_group_is_rejected(tmp_path):
    class System:
        state=tmp_path
        def run(self,args,**kw):
            if args[0]=='getent':return SimpleNamespace(returncode=0,stdout='ana:x:1000:100:nas-setup-owner:/home/ana:/usr/sbin/nologin\n')
            return SimpleNamespace(returncode=0,stdout='users sudo\n')
    # Marker intentionally has no colon: GECOS is one passwd field.
    with pytest.raises(SetupError):omv.OmvProvisioner(System())._verify_user('ana','owner')


def test_apply_does_not_silently_deploy_unrelated_configuration(tmp_path):
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    adapter._config_digest='before';adapter._digest=lambda:'external-change'
    adapter._rpc=lambda *args:pytest.fail('cannot deploy unrelated change')
    with pytest.raises(SetupError,match='omv_configuration_changed'):adapter._apply()


def test_status_checks_volume_even_for_single_disk(tmp_path):
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path,inventory=lambda:{'volumes':[]}))
    with pytest.raises(SetupError,match='volume_missing'):
        adapter.status({'volume':'gone','shares':[],'raid':{'state':'healthy','progress':100,'tolerance':0}})


def test_storage_only_existing_volume_returns_verified_resources(tmp_path):
    from nas_setup.journal import OperationJournal
    class Adapter(omv.OmvProvisioner):
        def _require_validated(self):pass
        def _verify_packages(self,raid):pass
        def _digest(self):return 'same-config'
        def _rpc(self,op,params=None):
            assert op=='config_dirty';return False
        def _mounted(self,identity):return {'id':identity,'path':str(tmp_path),'fstype':'ext4','mount_uuid':'mount-uuid'}
    selection={'storage':{'mode':'existing','volume':'fs-id','disks':[]},'users':[],'shares':[],
               'apps':[],'timezone':'UTC','machine_learning':False}
    adapter=Adapter(SimpleNamespace(state=tmp_path))
    resources=adapter.provision(selection,{'selection':selection,'targets':[],'tolerance':0},OperationJournal(tmp_path/'journal'),{})
    assert resources['volume']=='fs-id' and resources['shares']==[]
    assert resources['raid']=={'state':'healthy','progress':100,'tolerance':0}
    assert resources['mount_path']==str(tmp_path)


def test_acl_verification_checks_denied_user_and_default_inheritance(tmp_path):
    path=tmp_path/'share';path.mkdir()
    good='\n'.join(['user::rwx','user:ana:rwx','user:bob:r-x','user:eve:---','group::---','mask::rwx','other::---',
        'default:user::rwx','default:user:ana:rwx','default:user:bob:r-x','default:user:eve:---',
        'default:group::---','default:mask::rwx','default:other::---'])
    system=SimpleNamespace(state=tmp_path,run=lambda *a,**kw:SimpleNamespace(stdout=good))
    adapter=omv.OmvProvisioner(system)
    adapter._verify_acl(str(path),{'ana':'rw','bob':'ro','eve':'none'})
    system.run=lambda *a,**kw:SimpleNamespace(stdout=good.replace('default:user:eve:---','default:user:eve:rwx'))
    with pytest.raises(SetupError,match='share_permissions_changed'):
        adapter._verify_acl(str(path),{'ana':'rw','bob':'ro','eve':'none'})


def test_share_requests_explicit_smb_denials_and_no_private_app_parent(tmp_path):
    from nas_setup.journal import OperationJournal
    operations=[];registered={}
    class Adapter(omv.OmvProvisioner):
        def _db(self,model):return []
        def _mutate(self,op,params):
            operations.append((op,params))
            if op=='share_set':
                (tmp_path/params['reldirpath']).mkdir();registered.update(params,uuid='share-uuid');return registered.copy()
            if op=='smb_share_set':return dict(params,uuid='smb-uuid')
            if op=='acl_set':return '/tmp/bgstatus'
            return {}
        def _wait_bg(self,handle):pass
        def _verify_share(self,share,volume):return share
        def _verify_acl(self,*a,**kw):pass
        def _rpc(self,op,params=None):
            if op=='share_get':return registered.copy()
            if op=='privileges_get':return [{'name':'ana','perms':7,'type':'user'},{'name':'eve','perms':0,'type':'user'}]
            raise AssertionError(op)
    adapter=Adapter(SimpleNamespace(state=tmp_path))
    shared=adapter._share({'name':'Media','folder':'media','permissions':{'ana':'rw','eve':'none'}},
        {'id':'fs-id','path':str(tmp_path),'mount_uuid':'mount-uuid'},'owner',OperationJournal(tmp_path/'journal'))
    assert Path(shared['path']).parent==tmp_path and '.nas-setup' not in shared['path']
    smb=next(p for op,p in operations if op=='smb_share_set')
    assert smb['guest']=='no' and smb['inheritacls'] is True
    assert 'valid users = ana' in smb['extraoptions'] and 'invalid users = eve' in smb['extraoptions']
    acl=next(p for op,p in operations if op=='acl_set')
    assert acl['groupperms']==acl['otherperms']==0


def test_omv_privilege_rows_include_ids_and_unset_accounts():
    actual=[{'type':'user','id':1000,'name':'ana','perms':7},
            {'type':'user','id':1001,'name':'eve','perms':0},
            {'type':'user','id':1002,'name':'unmanaged','perms':None},
            {'type':'group','id':100,'name':'users','perms':None}]
    assert omv.explicit_privileges(actual)==[{'type':'user','name':'ana','perms':7},{'type':'user','name':'eve','perms':0}]
    actual[-1]['perms']=7
    assert {'type':'group','name':'users','perms':7} in omv.explicit_privileges(actual)


def test_bridge_calls_remote_omv_engine():
    source=Path(omv.__file__).with_name('omv_bridge.php').read_text()
    assert 'OMV\\Rpc\\Rpc::MODE_REMOTE' in source


def test_status_rejects_filesystem_uuid_cloned_to_different_physical_disk(tmp_path):
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    adapter._mounted=lambda identity:{'id':identity,'path':str(tmp_path)}
    adapter._discover=lambda:({'blockdevices':[]},[])
    with pytest.raises(SetupError,match='storage_changed'):
        adapter.status({'volume':'same-uuid','mount_path':str(tmp_path),'shares':[],
            'targets':[{'id':'/dev/disk/by-id/ata-original'}],'raid':{'tolerance':0}})


def test_owned_pending_config_checkpoint_allows_resume_but_external_change_refused(tmp_path):
    from nas_setup.journal import OperationJournal
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    dirty=False;digest='initial'
    adapter._digest=lambda:digest
    adapter._rpc=lambda *a,**kw:dirty
    journal=OperationJournal(tmp_path/'journal')
    adapter._start_context(journal)
    dirty=True;digest='own-change';adapter._config_digest=digest;adapter._save_context()
    adapter._start_context(journal)
    digest='admin-change'
    with pytest.raises(SetupError,match='omv_configuration_changed'):adapter._start_context(journal)


def test_partition_layout_change_invalidates_review_even_without_filesystem_signature(tmp_path):
    tree,ids=topology()
    child={'name':'/dev/sdb1','type':'part','size':500000,'mountpoints':[]}
    tree['blockdevices'][1]['children']=[child]
    before=omv.classify_disks(tree,ids,{})[1]
    child['size']=600000
    after=omv.classify_disks(tree,ids,{})
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path));adapter._discover=lambda:(tree,after)
    with pytest.raises(SetupError,match='storage_changed'):adapter._check_target(before)


def test_raid_pending_sync_is_not_misreported_as_healthy_or_degraded():
    assert omv.raid_health('resync','0','none',1)=={'state':'syncing','progress':0,'tolerance':1}


def test_all_read_only_share_accepts_calculated_acl_mask(tmp_path):
    path=tmp_path/'readonly';path.mkdir()
    entries={'user::rwx','user:ana:r-x','user:eve:---','group::---','mask::r-x','other::---'}
    entries|={'default:'+line for line in set(entries)}
    system=SimpleNamespace(state=tmp_path,run=lambda *a,**kw:SimpleNamespace(stdout='\n'.join(entries)))
    omv.OmvProvisioner(system)._verify_acl(str(path),{'ana':'ro','eve':'none'})


def test_format_cannot_adopt_preexisting_filesystem_uuid(tmp_path):
    from nas_setup.journal import OperationJournal
    class Adapter(omv.OmvProvisioner):
        def _filesystem_uuids(self,target):return ['old-uuid']
        def _mutate(self,*a):return '/tmp/bgstatusABC'
        def _wait_bg(self,*a):pass
        def _filesystem_for(self,target):return {'uuid':'old-uuid','devicefile':'/dev/sdb1','type':'ext4'}
    with pytest.raises(SetupError,match='recovery_required'):
        Adapter(SimpleNamespace(state=tmp_path))._format('/dev/sdb',OperationJournal(tmp_path/'journal'),lambda:None)


def test_recovery_review_verifies_owned_filesystem_and_never_formats(tmp_path):
    from nas_setup.journal import OperationJournal
    journal=OperationJournal(tmp_path/'journal')
    journal.perform('filesystem.dispatch',lambda:{'handle':'/tmp/bgstatus'})
    journal.perform('filesystem.result',lambda:{'uuid':'owned','devicefile':'/dev/sdb1','type':'ext4'})
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    adapter._filesystem_for=lambda target:{'uuid':'owned','devicefile':'/dev/sdb1','type':'ext4'}
    adapter._rpc=lambda *a:pytest.fail('read-only review must not call mutation RPC')
    adapter._check_target_identity=lambda *a,**kw:None
    plan={'selection':{'storage':{'mode':'single'}},'targets':[{'id':'/dev/disk/by-id/data'}]}
    adapter.review_recovery(plan,journal)
    adapter._filesystem_for=lambda target:{'uuid':'replaced','devicefile':'/dev/sdb1','type':'ext4'}
    with pytest.raises(SetupError,match='recovery_required'):adapter.review_recovery(plan,journal)


def test_recovery_review_rejects_unknown_dispatch(tmp_path):
    from nas_setup.journal import OperationJournal
    journal=OperationJournal(tmp_path/'journal')
    with pytest.raises(RuntimeError):journal.perform('filesystem.dispatch',lambda:(_ for _ in ()).throw(RuntimeError()))
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    with pytest.raises(SetupError,match='recovery_required'):
        adapter.review_recovery({'selection':{'storage':{'mode':'single'}},'targets':[{'id':'/dev/disk/by-id/data'}]},journal)


def test_degraded_array_verification_allows_only_missing_owned_members(tmp_path):
    tree,ids=topology();members=[dict(omv.classify_disks(tree,ids,{})[1],id='/dev/sdb')]
    members.append(dict(members[0],id='/dev/sdc',serial='sdc-serial',device='/dev/sdc',devicefile='/dev/sdc'))
    array={'uuid':'md-owned','devicefile':'/dev/md9','level':'raid1','numdevices':2,'devices':['/dev/sdb']}
    tree['blockdevices'][1]['children']=[{'name':'/dev/md9','type':'raid1','mountpoints':['/srv/data']}]
    current=omv.classify_disks(tree,ids,{'/dev/sdb':['md9']});current[1]['id']='/dev/sdb'
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    adapter._discover=lambda:(tree,current)
    adapter._rpc=lambda *a: [array]
    expected=dict(array,devices=['/dev/sdb','/dev/sdc'])
    adapter._verify_array(expected,members,allow_mounted=True,allow_degraded=True)
    array['devices']=['/dev/sdb','/dev/foreign']
    with pytest.raises(SetupError,match='storage_changed'):
        adapter._verify_array(expected,members,allow_mounted=True,allow_degraded=True)


def test_preflight_rejects_existing_user_before_storage_mutations(tmp_path):
    class System:
        state=tmp_path
        def run(self,args,**kw):
            assert args==['getent','passwd','ana']
            return SimpleNamespace(returncode=0,stdout='ana:x:1000:100:Existing:/home/ana:/bin/bash')
    adapter=omv.OmvProvisioner(System())
    adapter._mutate=lambda *a:pytest.fail('preflight may not mutate')
    with pytest.raises(SetupError,match='existing_installation'):
        adapter._preflight_conflicts({'users':[{'name':'ana'}],'shares':[],'storage':{'mode':'single'}})


def test_preflight_rejects_global_share_conflict_before_erasure(tmp_path):
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    adapter._db=lambda model:[{'name':'FILES','mntentref':'other-mount','reldirpath':'unrelated/'}]
    with pytest.raises(SetupError,match='existing_installation'):
        adapter._preflight_conflicts({'users':[],'shares':[{'name':'Files','folder':'files'}],'storage':{'mode':'raid1'}})


def test_completed_async_task_settles_udev_before_identity_read(tmp_path):
    calls=[]
    def run(args,**kwargs):calls.append(args);return SimpleNamespace(returncode=0,stdout='')
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path,run=run))
    adapter._rpc=lambda *a:{'running':False,'pos':0,'pendingOutput':False}
    adapter._wait_bg('/tmp/bgstatus-owned')
    assert calls==[['udevadm','settle','--timeout=30']]


def test_creation_uuid_is_translated_by_bridge_from_installed_omv_configuration():
    # OMV's configurable new-object sentinel is not the nil UUID; submitting a
    # nil UUID attempts to update a nonexistent object instead of creating it.
    assert omv.NEW_UUID=='new'
    bridge=Path(omv.__file__).with_name('omv_bridge.php').read_text()
    assert "\\OMV\\Environment::get('OMV_CONFIGOBJECT_NEW_UUID')" in bridge
    assert "['share_set', 'smb_share_set']" in bridge


def test_degraded_array_can_have_detached_original_member_still_present(tmp_path):
    tree,ids=topology(disk('sdc'))
    disks=omv.classify_disks(tree,ids,{})
    targets=[dict(d,id=d['devicefile']) for d in disks[1:]]
    tree['blockdevices'][1]['children']=[{'name':'/dev/md9','type':'raid1','mountpoints':['/srv/data']}]
    current=omv.classify_disks(tree,ids,{'/dev/sdb':['md9']})
    for d in current:d['id']=d['devicefile']
    array={'uuid':'md-owned','devicefile':'/dev/md9','level':'raid1','numdevices':2,'devices':['/dev/sdb']}
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path))
    adapter._discover=lambda:(tree,current);adapter._rpc=lambda *a:[array]
    adapter._verify_array(array,targets,allow_mounted=True,allow_degraded=True)
    current[2]['reason']='active_holder'
    with pytest.raises(SetupError,match='storage_changed'):
        adapter._verify_array(array,targets,allow_mounted=True,allow_degraded=True)


@pytest.mark.parametrize('raid,versions,accepted',[
    (False,{'openmediavault':'8.5.12-1'},True),
    (True,{'openmediavault':'8.5.12-1','openmediavault-md':'8.1.6-1'},True),
    (False,{'openmediavault':'8.5.13-1'},False),
    (True,{'openmediavault':'8.5.12-1','openmediavault-md':'8.1.7-1'},False),
    (True,{'openmediavault':'8.5.12-1'},False),
])
def test_exact_validated_package_versions_required(tmp_path,raid,versions,accepted):
    calls=[]
    def run(args,**kw):
        calls.append(args)
        assert args[:3]==['dpkg-query','--show','--showformat=${db:Status-Status}\t${Version}']
        package=args[3]
        return SimpleNamespace(returncode=0 if package in versions else 1,
            stdout='installed\t'+versions.get(package,''))
    adapter=omv.OmvProvisioner(SimpleNamespace(state=tmp_path,run=run))
    if accepted:adapter._verify_packages(raid)
    else:
        with pytest.raises(SetupError,match='omv_version_unsupported'):adapter._verify_packages(raid)
    if not raid:assert not any(call[3]=='openmediavault-md' for call in calls)


@pytest.mark.parametrize('operation,expected_timeout',[('config_apply',1740),('disk_list',180)])
def test_php_bridge_allows_bounded_slow_config_apply(tmp_path,operation,expected_timeout):
    import shutil,subprocess
    php=shutil.which('php')
    if not php:pytest.skip('PHP CLI required for bridge contract execution')
    includes=tmp_path/'openmediavault';includes.mkdir()
    (includes/'globals.inc').write_text('<?php define("OMV_ROLE_ADMINISTRATOR", 1);')
    (includes/'functions.inc').write_text('<?php')
    (includes/'autoloader.inc').write_text('''<?php
namespace OMV {
class Environment {
    private static $values = ['OMV_ENGINED_SO_RCVTIMEO' => 180];
    public static function get($name, $default = null) { return self::$values[$name] ?? $default; }
    public static function set($name, $value) { self::$values[$name] = $value; }
}}
namespace OMV\\Rpc {
class Rpc {
    const MODE_REMOTE = 2;
    public static function call($service, $method, $params, $context, $mode) {
        return ['timeout' => \\OMV\\Environment::get('OMV_ENGINED_SO_RCVTIMEO'), 'service' => $service];
    }
}}
''')
    # Test only: replace the identity check inside this unprivileged PHP process.
    # No real OMV implementation, daemon or device is accessible to the stub.
    bootstrap=tmp_path/'bootstrap.php'
    bootstrap.write_text('<?php function posix_geteuid() { return 0; }')
    result=subprocess.run([php,'-d','disable_functions=posix_geteuid','-d','auto_prepend_file='+str(bootstrap),
        '-d','include_path='+str(tmp_path),str(omv.BRIDGE)],input=json.dumps({'operation':operation,'params':None}),
        text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'ok':True,'result':{'timeout':expected_timeout,
        'service':'Config' if operation=='config_apply' else 'DiskMgmt'}}
