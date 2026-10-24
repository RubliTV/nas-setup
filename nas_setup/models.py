"""Validated requests and a side-effect-free installation planner."""
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator

class SetupError(Exception):
    def __init__(self, code, detail=''):
        super().__init__(code)
        self.code, self.detail = code, detail

class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    apps: list[Literal['tailscale', 'immich', 'jellyfin']] = Field(min_length=1, max_length=3)
    volume: str | None = Field(default=None, max_length=128)
    database_volume: str | None = Field(default=None, max_length=128)
    machine_learning: bool = False
    timezone: str = 'America/Sao_Paulo'

    @field_validator('apps')
    @classmethod
    def unique(cls, value):
        if len(value)!=len(set(value)): raise ValueError('duplicate apps')
        return value

    @field_validator('timezone')
    @classmethod
    def zone(cls,value):
        try: ZoneInfo(value)
        except (ZoneInfoNotFoundError,ValueError): raise ValueError('invalid timezone')
        return value

ML_FLAGS = {'ssse3','sse4_1','sse4_2','popcnt','cx16','lahf_lm'}

def make_plan(selection: Selection, inventory: dict) -> dict:
    from .storage import volume_path
    if not inventory.get('supported'): raise SetupError('unsupported_host')
    media = any(a in selection.apps for a in ('immich','jellyfin'))
    warnings=[]
    if media:
        if not selection.volume: raise SetupError('storage_required')
        volume_path(selection.volume,inventory)
        vol=next(v for v in inventory['volumes'] if v['id']==selection.volume)
        if vol['free_gb']<10: raise SetupError('storage_full')
    if 'immich' in selection.apps:
        if inventory['memory_gb']<6: raise SetupError('memory_low')
        if not selection.database_volume: raise SetupError('database_required')
        volume_path(selection.database_volume, inventory)
        db=next(v for v in inventory['volumes'] if v['id']==selection.database_volume)
        if db['free_gb']<10: raise SetupError('storage_full')
        if db.get('rotational',True): warnings.append('database_hdd')
        if selection.machine_learning and not ML_FLAGS.issubset(set(inventory['cpu_flags'])):
            raise SetupError('cpu_ml_unsupported')
        warnings.append('immich_heavy')
    steps=['preflight']+(['docker'] if media else [])
    steps += [a for a in ('immich','jellyfin','tailscale') if a in selection.apps]
    steps += ['verify']
    return {'apps':selection.apps,'steps':steps,'warnings':warnings,'selection':selection.model_dump()}
