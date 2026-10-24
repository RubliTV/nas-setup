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
