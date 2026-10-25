#!/usr/bin/env python3
"""Read-only baseline probe; run manually inside the disposable OMV VM.

This does not certify provisioning. It intentionally fails outside the named
throwaway guest and does not alter adapter gates or any disk contents.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def main():
    if socket.gethostname() != 'nas-v02-vm' or os.geteuid() != 0:
        raise SystemExit('Run as root inside the nas-v02-vm disposable guest only.')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('VERSION_ID', '').strip('"') != '13' or platform.machine() != 'x86_64':
        raise SystemExit('Expected Debian 13 amd64 guest.')
    tree = json.loads(run('lsblk', '--json', '--bytes', '--paths', '--output',
                         'NAME,TYPE,SIZE,SERIAL,TRAN,MOUNTPOINTS,FSTYPE,UUID'))
    disks = [d for d in tree['blockdevices'] if d['type'] == 'disk']
    data = {d.get('serial'): d for d in disks if (d.get('serial') or '').startswith('NASV02DATA')}
    expected = {f'NASV02DATA{i}' for i in range(1, 5)}
    if set(data) != expected or any(d.get('tran') != 'sata' or d['size'] < 16 * 1024**3 for d in data.values()):
        raise SystemExit('Expected four separate 16 GiB SATA virtual data disks.')
    system = [d for d in disks if d.get('serial') == 'NASV02SYSTEM']
    if len(system) != 1:
        raise SystemExit('Expected distinct virtual OS disk.')
    def mounts(d):
        return set(d.get('mountpoints') or []) | set().union(*(mounts(c) for c in d.get('children', [])))
    if '/' not in mounts(system[0]) or any(mounts(d) - {None} for d in data.values()):
        raise SystemExit('Baseline requires OS root ancestry and untouched, unmounted data disks.')
    packages = run('dpkg-query', '-W', '-f=${Package}\t${Version}\n', 'openmediavault', 'openmediavault-md')
    sources = {}
    for name in ('diskmgmt.inc', 'filesystemmgmt.inc', 'mdmgmt.inc', 'sharemgmt.inc', 'usermgmt.inc', 'smb.inc'):
        path = Path('/usr/share/openmediavault/engined/rpc') / name
        if path.exists():
            sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {'kind': 'read_only_environment_probe', 'provisioning_certified': False,
              'hostname': socket.gethostname(), 'machine': platform.machine(),
              'debian': release.get('VERSION_ID'), 'packages': packages.splitlines(),
              'lsblk': tree, 'rpc_source_sha256': sources}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
