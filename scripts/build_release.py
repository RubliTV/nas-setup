#!/usr/bin/env python3
"""Create a checksummed source release; excludes demo state and credentials."""
import argparse
import hashlib
import re
from pathlib import Path
import shutil
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default=str(ROOT/'dist'))
    p.add_argument('--repository',help='GitHub owner/repository for a downloadable install.sh')
    args=p.parse_args()
    if args.repository and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',args.repository):
        p.error('repository must be GitHub owner/repository')
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
    helper=out/'install.sh'
    if args.repository:
        url=f'https://github.com/{args.repository}/releases/download/v0.1.0/{target.name}'
        template=(ROOT/'packaging/download.sh.in').read_text()
        helper.write_text(template.replace('@ARCHIVE_URL@',url).replace('@ARCHIVE_SHA256@',hashlib.sha256(target.read_bytes()).hexdigest()))
        helper.chmod(0o755)
    else:
        # Never leave a helper pointing at a different or stale archive.
        helper.unlink(missing_ok=True)
    print(target)
if __name__=='__main__':main()
