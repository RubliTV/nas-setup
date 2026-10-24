"""Side-effect-free v0.2 storage, users and sharing contract."""
import hashlib
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .models import SetupError, Selection, ML_FLAGS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StorageChoice(StrictModel):
    mode: Literal['existing', 'single', 'raid1', 'raid5', 'raid6'] = 'existing'
    disks: list[str] = Field(default_factory=list, max_length=16)
    volume: str | None = Field(default=None, max_length=128)

    @field_validator('disks')
    @classmethod
    def identities(cls, values):
        if len(set(values)) != len(values) or any(not v or len(v)>256 for v in values):
            raise ValueError('invalid disk identities')
        return values

    @model_validator(mode='after')
    def exclusive(self):
        if self.mode == 'existing' and self.disks:
            raise ValueError('existing volume cannot have erasure targets')
        if self.mode != 'existing' and self.volume:
            raise ValueError('new storage cannot adopt an existing volume')
        return self


class SharingUser(StrictModel):
    name: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,30}$')

    @field_validator('name')
    @classmethod
    def ordinary(cls, value):
        if value in {'root','admin','nobody','nas-setup','daemon','bin','sys','sync','www-data','postgres','sshd','systemd-network'}:
            raise ValueError('reserved account')
        return value


class SharedFolder(StrictModel):
    name: str = Field(min_length=1,max_length=64)
    folder: Literal['files','media','backups','custom'] = 'custom'
    permissions: dict[str,Literal['rw','ro','none']] = Field(default_factory=dict)

    @field_validator('name')
    @classmethod
    def safe_name(cls, value):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,63}',value) or value != value.strip():
            raise ValueError('share name must use letters, digits, spaces, underscore or hyphen')
        if value.casefold() in {'global','homes','printers','print','root','admin'}:
            raise ValueError('reserved share')
        return value


class SetupSelection(StrictModel):
    storage: StorageChoice
    users: list[SharingUser] = Field(default_factory=list,max_length=32)
    shares: list[SharedFolder] = Field(default_factory=list,max_length=32)
    apps: list[Literal['tailscale','immich','jellyfin']] = Field(default_factory=list,max_length=3)
    timezone: str = 'America/Sao_Paulo'
    machine_learning: bool = False

    @model_validator(mode='after')
    def coherent(self):
        names=[u.name for u in self.users]
        shares=[s.name.casefold() for s in self.shares]
        folders=[s.folder for s in self.shares if s.folder!='custom']
        if len(set(names))!=len(names) or len(set(shares))!=len(shares) or len(set(folders))!=len(folders):
            raise ValueError('duplicate users or folders')
        if len(set(self.apps))!=len(self.apps):raise ValueError('duplicate apps')
        for share in self.shares:
            if not names or set(share.permissions)!=set(names):raise ValueError('explicit permission required for every user')
            if not any(v!='none' for v in share.permissions.values()):raise ValueError('share has no permitted user')
        # Reuse v1 timezone validation without changing the legacy API contract.
        Selection(apps=['tailscale'],timezone=self.timezone)
        if self.machine_learning and 'immich' not in self.apps:raise ValueError('machine learning requires Immich')
        return self


def plan_setup(selection: SetupSelection, inventory: dict) -> dict:
    if not inventory.get('supported'):raise SetupError('unsupported_host')
    mode=selection.storage.mode
    targets=[];warnings=[];tolerance=0
    if mode=='existing':
        if not selection.storage.volume:raise SetupError('storage_required')
        matches=[v for v in inventory.get('volumes',[]) if v['id']==selection.storage.volume]
        if len(matches)!=1:raise SetupError('volume_missing')
        vol=matches[0]
        if vol.get('fstype')!='ext4':raise SetupError('omv_filesystem_unsupported')
        capacity=int(vol.get('size_gb',0)*2**30)
        if any(a in selection.apps for a in ('immich','jellyfin')) and vol.get('free_gb',0)<10:
            raise SetupError('storage_full')
        warnings.append('existing_protection_unknown')
    else:
        count=len(selection.storage.disks)
        limits={'single':(1,1),'raid1':(2,2),'raid5':(3,16),'raid6':(4,16)}
        if not limits[mode][0]<=count<=limits[mode][1]:raise SetupError('disk_count')
        for identity in selection.storage.disks:
            matches=[d for d in inventory.get('disks',[]) if d['id']==identity]
            if len(matches)!=1:raise SetupError('disk_identity')
            disk=matches[0]
            if not disk.get('eligible'):raise SetupError('disk_unavailable')
            if not disk.get('serial') or disk.get('size_bytes',0)<=0:raise SetupError('disk_identity')
            if mode!='single' and disk.get('transport') not in {'sata','sas','nvme'}:
                raise SetupError('disk_transport')
            targets.append(dict(disk))
        if len({d['serial'] for d in targets})!=count:raise SetupError('disk_identity')
        tolerance={'single':0,'raid1':1,'raid5':1,'raid6':2}[mode]
        capacity=min(d['size_bytes'] for d in targets)*(1 if mode=='raid1' else count-tolerance)
        if len({d['size_bytes'] for d in targets})>1:warnings.append('mixed_disk_sizes')
        if capacity<10*2**30:raise SetupError('storage_full')
        warnings+=['erase_selected_disks','redundancy_not_backup']
    if not inventory.get('provisioning_enabled',False):warnings.append('provisioning_unvalidated')
    if 'immich' in selection.apps:
        if inventory.get('memory_gb',0)<6:raise SetupError('memory_low')
        if selection.machine_learning and not ML_FLAGS.issubset(set(inventory.get('cpu_flags',[]))):
            raise SetupError('cpu_ml_unsupported')
        warnings.append('immich_heavy')
    steps=['storage_prerequisites','storage_setup']
    if any(a in selection.apps for a in ('immich','jellyfin')):steps.append('docker')
    steps += [a for a in ('immich','jellyfin','tailscale') if a in selection.apps]
    steps.append('verify')
    if mode.startswith('raid'):steps.append('raid_sync')
    return {'selection':selection.model_dump(),'targets':targets,'capacity_bytes':capacity,'tolerance':tolerance,
            'destructive':mode!='existing','warnings':warnings,'steps':steps}


def inventory_fingerprint(selection: SetupSelection, inventory: dict) -> str:
    """Only stable identity/safety attributes; background disk usage is irrelevant."""
    plan=plan_setup(selection,inventory)
    keys=('id','serial','wwn','size_bytes','transport','eligible','reason','signatures','devicefile','logical_sector','physical_sector','topology')
    snapshot={'supported':inventory.get('supported'),'enabled':inventory.get('provisioning_enabled'),
              'targets':[{k:d.get(k) for k in keys} for d in plan['targets']]}
    if selection.storage.mode=='existing':
        snapshot['volume']=[{k:v.get(k) for k in ('id','path','fstype')} for v in inventory['volumes'] if v['id']==selection.storage.volume]
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()).hexdigest()
