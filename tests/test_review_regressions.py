from pathlib import Path
import json
import os
import subprocess
import pytest
from nas_setup.auth import issue_code
from nas_setup.models import Selection,SetupError
from nas_setup.system import System


def test_pairing_code_does_not_follow_preplanted_temp_symlink(tmp_path):
    victim=tmp_path/'important';victim.write_text('do not replace')
    web=tmp_path/'web';web.mkdir();(web/'access.tmp').symlink_to(victim)
    issue_code(web)
    assert victim.read_text()=='do not replace'


def test_guard_rejects_uuid_moved_to_another_mountpoint(tmp_path):
    system=System(tmp_path/'worker');project=system.project('jellyfin');project.mkdir()
    selection=Selection(apps=['jellyfin'],volume='disk-id')
    (project/'selection.json').write_text(selection.model_dump_json())
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    (project/'mounts.json').write_text(json.dumps({'disk-id':str(old)}))
    system.inventory=lambda:{'volumes':[{'id':'disk-id','path':str(new),'fstype':'ext4'}]}
    assert hasattr(system,'guard_stack'),'Stack mount identity guard is missing'
    with pytest.raises(SetupError,match='storage_changed'):system.guard_stack('jellyfin')


def test_unmanaged_project_cannot_be_adopted(tmp_path):
    system=System(tmp_path/'worker')
    assert hasattr(system,'check_project_ownership'),'Docker project ownership check is missing'
    system.docker_available=lambda:True
    def run(args,**kwargs):
        if args[:3]==['docker','ps','-aq']:return subprocess.CompletedProcess(args,0,'container-id\n','')
        if args[:2]==['docker','inspect']:
            return subprocess.CompletedProcess(args,0,json.dumps([{'Config':{'Labels':{'com.docker.compose.project':'nas-setup-jellyfin'}},'State':{'Running':False}}]),'')
        raise AssertionError(args)
    system.run=run
    with pytest.raises(SetupError,match='existing_installation'):system.check_project_ownership('jellyfin')


def test_empty_existing_secret_is_rejected(tmp_path):
    from nas_setup.system import write_secret
    path=tmp_path/'password';path.touch(mode=0o600)
    with pytest.raises(SetupError,match='unsafe_storage'):write_secret(path)


def test_command_failure_preserves_redacted_diagnostics(tmp_path,monkeypatch):
    system=System(tmp_path/'worker')
    secret='a'*64
    project=system.project('immich');project.mkdir()
    (project/'db-password').write_text(secret)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,1,'',f'Failed password={secret} https://login.tailscale.com/a/private'))
    with pytest.raises(SetupError):system.run(['apt-get','update'])
    report=(system.state/'diagnostics.log').read_text()
    assert 'apt-get' in report and 'Failed' in report
    assert secret not in report and '/a/private' not in report


def test_port_conflict_is_detected(tmp_path):
    import socket
    system=System(tmp_path/'worker')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        with pytest.raises(SetupError,match='port_conflict'):
            system.check_port(listener.getsockname()[1])


def test_owned_container_with_changed_mount_is_rejected(tmp_path):
    import yaml
    from nas_setup.system import write_secret
    system=System(tmp_path/'worker');project=system.project('jellyfin');project.mkdir()
    owner=write_secret(system.state/'installation-id')
    (project/'compose.yml').write_text(yaml.safe_dump({'services':{'jellyfin':{'image':'jellyfin/jellyfin:12.3','volumes':[{'type':'bind','source':'/safe','target':'/data'}]}}}))
    container={'Config':{'Image':'jellyfin/jellyfin:12.3','Labels':{'io.nas-setup.owner':owner,'com.docker.compose.service':'jellyfin'}},'State':{'Running':True},'Mounts':[{'Type':'bind','Source':'/wrong','Destination':'/data','RW':True}]}
    system.docker_available=lambda:True
    system.run=lambda args,**kwargs:subprocess.CompletedProcess(args,0,'container-id' if args[1]=='ps' else json.dumps([container]),'')
    with pytest.raises(SetupError,match='existing_installation'):system.check_project_ownership('jellyfin')
    container['Mounts'][0]['Source']='/safe'
    assert system.check_project_ownership('jellyfin') is True
