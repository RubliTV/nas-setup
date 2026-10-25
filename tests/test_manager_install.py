"""Installer account bootstrap on the upgrade path, with host writes isolated."""
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


def test_update_prompts_for_missing_account_interactively_after_runtime_updated(tmp_path,monkeypatch,capsys):
    source=tmp_path/'release';source.mkdir();(source/'README').write_text('fixture')
    (source/'SHA256SUMS').write_text(hashlib.sha256(b'fixture').hexdigest()+'  README\n')
    release=tmp_path/'os-release';release.write_text('ID=debian\nVERSION_ID="13"\n')
    script=Path(__file__).resolve().parents[1]/'packaging/install.py'
    spec=importlib.util.spec_from_file_location('installer',script)
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    monkeypatch.setattr(installer,'ROOT',source)
    monkeypatch.setattr(installer,'Path',lambda value:release if str(value)=='/etc/os-release' else Path(value))
    monkeypatch.setattr(installer.platform,'system',lambda:'Linux')
    monkeypatch.setattr(installer.os,'geteuid',lambda:0)
    calls=[]
    def run(*args):
        if args[0]=='dpkg':return 'amd64'
        if args[0]=='dpkg-query':return '8.5'
        if args==('/opt/nas-setup/venv/bin/nas-setup','reopen'):
            assert calls==['updated','prompted'];return 'https://192.168.1.10:8443'
        raise AssertionError(args)
    monkeypatch.setattr(installer,'run',run)
    def update(*args,**kwargs):calls.append('updated');return 'old pairing output'
    updater=SimpleNamespace(update_release=update,UpdateError=RuntimeError)
    fake_spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
    monkeypatch.setattr(installer.importlib.util,'spec_from_file_location',lambda *args:fake_spec)
    monkeypatch.setattr(installer.importlib.util,'module_from_spec',lambda spec:updater)
    def prompt(argv,**kwargs):
        assert calls==['updated']
        assert argv==['/opt/nas-setup/venv/bin/nas-setup','manager-account','--if-missing']
        assert kwargs=={'check':True}
        calls.append('prompted');return SimpleNamespace(returncode=0)
    monkeypatch.setattr(installer.subprocess,'run',prompt)
    monkeypatch.setattr(sys,'argv',['install.py','--update'])
    installer.main()
    assert calls==['updated','prompted']
    output=capsys.readouterr().out
    assert 'https://192.168.1.10:8443' in output
    assert 'old pairing output' not in output
