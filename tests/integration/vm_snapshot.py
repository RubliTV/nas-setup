#!/usr/bin/env python3
"""Control snapshots of the fixed disposable QEMU guest, never host disks."""
import argparse
from pathlib import Path
import socket
import time

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=('save', 'restore', 'list'))
args = parser.parse_args()
path = Path('/tmp/nas-v02-vm/monitor.sock')
if not path.is_socket():
    raise SystemExit('The disposable QEMU monitor socket is unavailable.')
command = {'save': 'savevm omv-clean', 'restore': 'loadvm omv-clean', 'list': 'info snapshots'}[args.action]
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(60)
    connection.connect(str(path))
    greeting = bytearray()
    while b'(qemu)' not in greeting:
        greeting.extend(connection.recv(8192))
    connection.sendall((command + '\n').encode())
    output = bytearray()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        chunk = connection.recv(65536)
        if not chunk:
            raise SystemExit('QEMU monitor disconnected.')
        output.extend(chunk)
        if b'(qemu)' in output:
            break
    response = output.decode(errors='replace').partition('\n')[2].replace('\r', '').replace('(qemu)', '').strip()
    print(response or 'Snapshot command completed.')
    if any(word in response.lower() for word in ('error', 'failed', 'could not')):
        raise SystemExit(1)
