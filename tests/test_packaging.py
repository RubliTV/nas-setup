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
        assert not any('/.demo/' in n or '/.venv/' in n or n.endswith('access.json') for n in names)
