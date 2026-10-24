"""Only storage discovered by the server may be selected by clients."""
from pathlib import Path
from .models import SetupError

LOCAL_FILESYSTEMS={'ext4','ext3','xfs','btrfs','zfs'}

def volume_path(identity, inventory):
    volume=next((v for v in inventory['volumes'] if v['id']==identity),None)
    if not volume: raise SetupError('volume_missing')
    path=Path(volume['path'])
    if (not path.is_absolute() or not path.is_dir() or path.resolve()!=path
            or volume['fstype'] not in LOCAL_FILESYSTEMS or path==Path('/')):
        raise SetupError('unsafe_storage')
    return path

def safe_directory(base: Path, *parts: str) -> Path:
    path=base.joinpath(*parts)
    if not path.resolve().is_relative_to(base) or any(p.is_symlink() for p in [path,*path.parents] if p!=base):
        raise SetupError('unsafe_storage')
    return path
