import json
from pathlib import Path
import pytest
from nas_setup.system import System
from nas_setup.models import SetupError


def configured(tmp_path):
    system=System(tmp_path/'worker');volume=tmp_path/'volume';volume.mkdir();media=volume/'shares'/'media';media.mkdir(parents=True)
    system.inventory=lambda:{'volumes':[{'id':'disk','path':str(volume),'fstype':'ext4'}]}
    resources={'volume':'disk','media_path':str(media),'shares':[{'uuid':'share-uuid','folder':'media','path':str(media)}]}
    assert hasattr(system,'approve_media'),'approved Media share guard missing'
    system.media_registration=lambda record:True
    return system,resources,media


def test_only_exact_registered_media_binding_allowed(tmp_path):
    system,resources,media=configured(tmp_path)
    system.approve_media(resources)
    assert system.media_binding()==media
    other=media.parent/'other';other.mkdir()
    record=system.project('jellyfin')/'approved-media.json'
    data=json.loads(record.read_text());data['path']=str(other);record.write_text(json.dumps(data))
    system.media_registration=lambda record:False
    with pytest.raises(SetupError,match='storage_changed'):system.media_binding()


def test_media_symlink_replacement_is_rejected(tmp_path):
    system,resources,media=configured(tmp_path);system.approve_media(resources)
    target=tmp_path/'elsewhere';target.mkdir();media.rmdir();media.symlink_to(target)
    with pytest.raises(SetupError,match='unsafe_storage'):system.media_binding()


def test_existing_jellyfin_cannot_reuse_writable_media_mount(tmp_path):
    import subprocess
    import yaml
    from nas_setup.system import write_secret
    system,resources,media=configured(tmp_path);system.approve_media(resources)
    owner=write_secret(system.state/'installation-id')
    project=system.project('jellyfin')
    (project/'compose.yml').write_text(yaml.safe_dump({'services':{'jellyfin':{'image':'jellyfin/test','volumes':[{'type':'bind','source':str(media),'target':'/media','read_only':True}]}}}))
    container={'Config':{'Image':'jellyfin/test','Labels':{'io.nas-setup.owner':owner,'com.docker.compose.service':'jellyfin'}},'State':{'Running':True},'Mounts':[{'Type':'bind','Source':str(media),'Destination':'/media','RW':True}]}
    system.docker_available=lambda:True
    system.run=lambda args,**kwargs:subprocess.CompletedProcess(args,0,'id' if args[1]=='ps' else json.dumps([container]),'')
    with pytest.raises(SetupError,match='existing_installation'):system.check_project_ownership('jellyfin')
    container['Mounts'][0]['RW']=False
    assert system.check_project_ownership('jellyfin') is True
