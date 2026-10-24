#!/usr/bin/env python3
"""Create a checksummed source release; excludes demo state and credentials."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default=str(ROOT/'dist'));args=p.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    name='nas-setup-0.1.0'
    with tempfile.TemporaryDirectory() as temp:
        package=Path(temp)/name;package.mkdir()
        for namepath in ['nas_setup','scripts','packaging','docs','pyproject.toml','requirements.lock','README.md']:
            source=ROOT/namepath
            if not source.exists():continue
            if source.is_dir():shutil.copytree(source,package/namepath,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            else:shutil.copy2(source,package/namepath)
        checks=[]
        for path in sorted(package.rglob('*')):
            if path.is_file():checks.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.relative_to(package).as_posix())
        (package/'SHA256SUMS').write_text('\n'.join(checks)+'\n')
        target=out/(name+'.tar.gz')
        with tarfile.open(target,'w:gz') as archive:archive.add(package,arcname=name)
    (out/(target.name+'.sha256')).write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n')
    print(target)
if __name__=='__main__':main()
