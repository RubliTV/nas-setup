import importlib.util
import pytest


def api():
    assert importlib.util.find_spec('nas_setup.setup_models'), 'v0.2 planner missing'
    from nas_setup.setup_models import SetupSelection, plan_setup
    from nas_setup.models import SetupError
    return SetupSelection,plan_setup,SetupError


def inventory():
    return {'supported':True,'memory_gb':8,'cpu_flags':[], 'provisioning_enabled':True,
        'volumes':[{'id':'existing','path':'/srv/data','fstype':'ext4','size_gb':1000,'free_gb':900}],
        'disks':[{'id':f'd{i}','model':'Test','serial':f'serial{i}','size_bytes':10**12,'transport':'sata','eligible':i>0,'reason':'system_disk' if i==0 else None,'signatures':[]} for i in range(5)]}


def choice(mode='raid1',disks=None):
    return {'storage':{'mode':mode,'disks':disks or ['d1','d2']},'users':[{'name':'ana'},{'name':'bruno'}],
        'shares':[{'name':'Files','folder':'files','permissions':{'ana':'rw','bruno':'ro'}}]}


@pytest.mark.parametrize('mode,count,size,tolerance',[('single',1,1,0),('raid1',2,1,1),('raid5',3,2,1),('raid6',4,2,2)])
def test_capacity_and_protection(mode,count,size,tolerance):
    Model,plan,_=api();result=plan(Model(**choice(mode,[f'd{i}' for i in range(1,count+1)])),inventory())
    assert result['capacity_bytes']==size*10**12
    assert result['tolerance']==tolerance
    assert result['destructive'] is True
    assert result['selection']['apps']==[]


def test_raid6_needs_four_disks():
    Model,plan,Error=api()
    with pytest.raises(Error,match='disk_count'):plan(Model(**choice('raid6')),inventory())


def test_system_disk_and_usb_raid_rejected():
    Model,plan,Error=api()
    with pytest.raises(Error,match='disk_unavailable'):plan(Model(**choice(disks=['d0','d1'])),inventory())
    inv=inventory();inv['disks'][1]['transport']='usb'
    with pytest.raises(Error,match='disk_transport'):plan(Model(**choice()),inv)


def test_missing_and_duplicate_identity_rejected():
    Model,plan,Error=api();inv=inventory();inv['disks'][2]['serial']=''
    with pytest.raises(Error,match='disk_identity'):plan(Model(**choice()),inv)
    inv=inventory();inv['disks'].append(inv['disks'][1].copy())
    with pytest.raises(Error,match='disk_identity'):plan(Model(**choice()),inv)


def test_permissions_must_name_existing_unique_users():
    Model,_,_=api();data=choice();data['shares'][0]['permissions']['stranger']='rw'
    with pytest.raises(ValueError):Model(**data)
    data=choice();data['users'].append({'name':'ana'})
    with pytest.raises(ValueError):Model(**data)


def test_password_not_part_of_public_selection():
    Model,_,_=api();data=choice();data['users'][0]['password']='private-password'
    with pytest.raises(ValueError):Model(**data)


def test_existing_volume_is_non_destructive():
    Model,plan,_=api();data=choice();data['storage']={'mode':'existing','volume':'existing'}
    result=plan(Model(**data),inventory())
    assert not result['destructive'] and result['targets']==[]
    assert 'raid_sync' not in result['steps']


@pytest.mark.parametrize('transport',['usb','virtio','scsi','unknown'])
def test_raid_requires_direct_supported_transport(transport):
    Model,plan,Error=api();inv=inventory();inv['disks'][1]['transport']=transport
    with pytest.raises(Error,match='disk_transport'):plan(Model(**choice()),inv)


def test_existing_volume_requires_validated_filesystem():
    Model,plan,Error=api();inv=inventory();inv['volumes'][0]['fstype']='xfs'
    data=choice();data['storage']={'mode':'existing','volume':'existing'}
    with pytest.raises(Error,match='omv_filesystem_unsupported'):plan(Model(**data),inv)


def test_partition_topology_change_invalidates_disk_fingerprint():
    from nas_setup.setup_models import inventory_fingerprint
    Model,_,_=api();inv=inventory();selection=Model(**choice())
    before=inventory_fingerprint(selection,inv)
    inv['disks'][1]['topology']='new-partition-table'
    assert inventory_fingerprint(selection,inv)!=before
