#!/usr/bin/env python3
"""Prepare a new fixed-directory macOS disposable VM; never overwrite a run."""
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess

DIRECTORY = Path('/tmp/nas-v02-vm')
QEMU_IMG = Path('/opt/homebrew/bin/qemu-img')
IMAGE_NAME = 'debian-13-genericcloud-amd64.qcow2'
BASE_URL = 'https://cloud.debian.org/images/cloud/trixie/latest/'


def run(*args):
    subprocess.run(args, check=True)


def main():
    if platform.system() != 'Darwin' or not QEMU_IMG.is_file():
        raise SystemExit('Requires macOS and QEMU installed at /opt/homebrew/bin.')
    if DIRECTORY.exists():
        raise SystemExit('Refusing to overwrite existing /tmp/nas-v02-vm. Preserve or remove it explicitly first.')
    DIRECTORY.mkdir(mode=0o700)
    seed = DIRECTORY / 'seed'
    seed.mkdir()
    image = DIRECTORY / 'debian-base.qcow2'
    sums = DIRECTORY / 'SHA512SUMS'
    run('curl', '-fL', '--retry', '2', '-o', str(image), BASE_URL + IMAGE_NAME)
    run('curl', '-fL', '--retry', '2', '-o', str(sums), BASE_URL + 'SHA512SUMS')
    expected = next(line.split()[0] for line in sums.read_text().splitlines() if line.split()[-1] == IMAGE_NAME)
    with image.open('rb') as stream:
        digest = hashlib.sha512()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise SystemExit('Debian image checksum mismatch; do not boot this download.')
    run('ssh-keygen', '-t', 'ed25519', '-N', '', '-f', str(DIRECTORY / 'id_ed25519'), '-C', 'nas-v02-disposable')
    key = (DIRECTORY / 'id_ed25519.pub').read_text().strip()
    (seed / 'meta-data').write_text('instance-id: nas-v02-vm-001\nlocal-hostname: nas-v02-vm\n')
    (seed / 'user-data').write_text(
        '#cloud-config\ndisable_root: false\nssh_pwauth: false\nusers:\n'
        '  - name: root\n    ssh_authorized_keys:\n      - ' + key + '\n'
        'write_files:\n  - path: /etc/ssh/sshd_config.d/01-disposable.conf\n'
        '    content: |\n      PermitRootLogin prohibit-password\n      PasswordAuthentication no\n'
        'runcmd:\n  - [systemctl, restart, ssh]\n')
    run('hdiutil', 'makehybrid', '-o', str(DIRECTORY / 'seed.iso'), str(seed), '-iso', '-joliet', '-default-volume-name', 'cidata')
    run(str(QEMU_IMG), 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(image), str(DIRECTORY / 'os.qcow2'), '16G')
    for number in range(1, 5):
        run(str(QEMU_IMG), 'create', '-f', 'qcow2', str(DIRECTORY / f'data{number}.qcow2'), '16G')
    shutil.copyfile(Path(__file__).with_name('start_disposable_qemu.sh'), DIRECTORY / 'start.sh')
    (DIRECTORY / 'start.sh').chmod(0o700)
    (DIRECTORY / 'ssh.sh').write_text(
        '#!/bin/sh\nexec /usr/bin/ssh -i /tmp/nas-v02-vm/id_ed25519 -p 22282 '
        '-o UserKnownHostsFile=/tmp/nas-v02-vm/known_hosts -o StrictHostKeyChecking=accept-new '
        '-o BatchMode=yes -o ConnectTimeout=10 root@127.0.0.1 "$@"\n')
    (DIRECTORY / 'ssh.sh').chmod(0o700)
    print('Prepared verified image and virtual disks. First boot: /tmp/nas-v02-vm/start.sh --with-seed')
    print('After SSH/cloud-init finishes, power off and restart without --with-seed before OMV tests.')


if __name__ == '__main__':
    main()
