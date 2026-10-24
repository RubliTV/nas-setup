#!/bin/sh
set -eu
cd /tmp/nas-v02-vm
exec /opt/homebrew/bin/qemu-system-x86_64 \
 -name nas-v02-disposable -machine q35 -accel tcg,thread=single -cpu max,tsc=off \
 -smp 1 -m 4096 -display none -daemonize -pidfile /tmp/nas-v02-vm/qemu.pid \
 -monitor unix:/tmp/nas-v02-vm/monitor.sock,server=on,wait=off \
 -serial file:/tmp/nas-v02-vm/serial.log \
 -netdev user,id=net0,hostfwd=tcp:127.0.0.1:22282-:22 \
 -device virtio-net-pci,netdev=net0 \
 -drive if=none,id=os,file=/tmp/nas-v02-vm/os.qcow2,format=qcow2 \
 -device virtio-blk-pci,drive=os,serial=NASV02SYSTEM,bootindex=1 \
 -drive if=none,id=data1,file=/tmp/nas-v02-vm/data1.qcow2,format=qcow2 \
 -device ide-hd,drive=data1,bus=ide.0,serial=NASV02DATA1 \
 -drive if=none,id=data2,file=/tmp/nas-v02-vm/data2.qcow2,format=qcow2 \
 -device ide-hd,drive=data2,bus=ide.1,serial=NASV02DATA2 \
 -drive if=none,id=data3,file=/tmp/nas-v02-vm/data3.qcow2,format=qcow2 \
 -device ide-hd,drive=data3,bus=ide.2,serial=NASV02DATA3 \
 -drive if=none,id=data4,file=/tmp/nas-v02-vm/data4.qcow2,format=qcow2 \
 -device ide-hd,drive=data4,bus=ide.3,serial=NASV02DATA4
