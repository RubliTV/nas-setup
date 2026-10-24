import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_bootstrap_refuses_unsupported_host_before_writes():
    script=ROOT/'scripts/bootstrap.sh'
    assert script.exists(),'Bootstrap is not implemented'
    result=subprocess.run(['bash',str(script),'--check'],capture_output=True,text=True)
    assert result.returncode!=0
    assert 'OMV 8' in result.stdout+result.stderr


def test_release_contains_verified_runtime_and_no_secrets(tmp_path):
    script=ROOT/'scripts/build_release.py'
    assert script.exists(),'Release builder is not implemented'
    result=subprocess.run([sys.executable,str(script),'--output',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    import tarfile
    archive=next(tmp_path.glob('*.tar.gz'))
    with tarfile.open(archive) as tar:
        names=tar.getnames()
        assert any(n.endswith('/scripts/bootstrap.sh') for n in names)
        assert any(n.endswith('/SHA256SUMS') for n in names)
        assert any(n.endswith('/nas_setup/omv_bridge.php') for n in names)
        assert all(n.startswith('nas-setup-0.2.0') for n in names)
        assert not any('/.demo/' in n or '/.venv/' in n or n.endswith('access.json') for n in names)


def test_github_release_helper_pins_archive_and_cleans_up(tmp_path):
    import hashlib
    result=subprocess.run([sys.executable,str(ROOT/'scripts/build_release.py'),'--output',str(tmp_path),'--repository','example/nas-setup'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    helper=tmp_path/'install.sh'
    assert helper.exists()
    script=helper.read_text()
    assert 'https://github.com/example/nas-setup/releases/download/v0.2.0/nas-setup-0.2.0.tar.gz' in script
    assert hashlib.sha256((tmp_path/'nas-setup-0.2.0.tar.gz').read_bytes()).hexdigest() in script
    assert 'sha256sum --check' in script and 'mktemp -d' in script and 'trap ' in script
    assert '192.168.' not in script
    assert subprocess.run(['bash','-n',str(helper)]).returncode==0


def test_release_rejects_invalid_repository(tmp_path):
    result=subprocess.run([sys.executable,str(ROOT/'scripts/build_release.py'),'--output',str(tmp_path),'--repository','owner/repo;bad'],capture_output=True,text=True)
    assert result.returncode!=0
    assert not (tmp_path/'install.sh').exists()


def test_bootstrap_runtime_is_accessible_under_restrictive_root_umask(tmp_path,monkeypatch):
    """A real venv and filesystem modes must work for the separate web-service UID."""
    import hashlib,importlib.util,json,os,stat,venv
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('nas_bootstrap',ROOT/'packaging/install.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    source=tmp_path/'release';source.mkdir()
    (source/'README.md').write_text('test release\n')
    (source/'SHA256SUMS').write_text(hashlib.sha256((source/'README.md').read_bytes()).hexdigest()+'  README.md\n')
    fake_root=tmp_path/'system'
    for directory in ('opt','etc/systemd/system','var/lib','usr/local/bin'):
        (fake_root/directory).mkdir(parents=True,exist_ok=True)
    (fake_root/'etc/os-release').write_text('ID=debian\nVERSION_ID="13"\n')
    def redirected_path(value):
        path=Path(value)
        return fake_root/str(path).lstrip('/') if path.is_absolute() else path
    class SocketProbe:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,address):pass
    def run(*args):
        if args[0]=='dpkg':return 'amd64\n'
        if args[0]=='dpkg-query':return '8.5.12-1\n'
        if args[0]=='ip':return json.dumps([{'addr_info':[{'family':'inet','local':'192.168.77.2'}]}])
        if args[:3]==('python3','-m','venv'):
            venv.EnvBuilder(with_pip=False).create(args[3]);return ''
        if args[:2]==('openssl','req'):
            Path(args[args.index('-keyout')+1]).write_text('test private key')
            Path(args[args.index('-out')+1]).write_text('test certificate');return ''
        if args[:2]==('openssl','x509'):return 'test fingerprint\n'
        if args[0] in ('apt-get','systemctl') or args[0].endswith('/pip'):return ''
        if args[0].endswith('/nas-setup') and args[1]=='reopen':return 'test pairing output\n'
        raise AssertionError('unexpected bootstrap command '+repr(args))
    monkeypatch.setattr(installer,'ROOT',source)
    monkeypatch.setattr(installer,'Path',redirected_path)
    monkeypatch.setattr(installer,'run',run)
    monkeypatch.setattr(installer.platform,'system',lambda:'Linux')
    monkeypatch.setattr(installer.os,'geteuid',lambda:0)
    monkeypatch.setattr(installer.os,'chown',lambda *args:None)
    monkeypatch.setattr(installer.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
    monkeypatch.setattr(installer.socket,'socket',lambda:SocketProbe())
    monkeypatch.setattr(sys,'argv',['install.py','--address','192.168.77.2'])
    previous=os.umask(0o077)
    try:installer.main()
    finally:os.umask(previous)
    def mode(path):return stat.S_IMODE((fake_root/path).stat().st_mode)
    for path in ('opt/nas-setup','opt/nas-setup/venv','opt/nas-setup/venv/bin','opt/nas-setup/venv/lib','var/lib/nas-setup'):
        assert mode(path)==0o755,path
    assert mode('var/lib/nas-setup/worker')==0o700
    assert mode('var/lib/nas-setup/web')==0o700
    assert mode('etc/nas-setup')==0o750
    assert mode('etc/nas-setup/server.key')==0o640
    assert mode('etc/systemd/system/nas-setup-web.service')==0o644
