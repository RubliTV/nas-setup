# Disposable live validation helpers

These helpers are manual infrastructure/probes, not pytest cases and not evidence
that a storage path passed. The dated results and missing coverage belong in
`docs/v02-vm-validation.md`.

On a macOS host with Homebrew QEMU already installed, run
`python3 tests/integration/prepare_disposable_qemu.py` to download the official
Debian image, check its published SHA-512, create the sparse virtual disks and
generate the temporary SSH/cloud-init files. The helper refuses to overwrite an
existing `/tmp/nas-v02-vm`. First boot uses `/tmp/nas-v02-vm/start.sh --with-seed`;
after cloud-init finishes, power off with the SSH helper and restart without that
flag. This bootstrap helper has syntax and existing-directory refusal checks;
the recorded live attempt was prepared with the equivalent commands directly.

`start_disposable_qemu.sh` starts the macOS QEMU VM from the dedicated
`/tmp/nas-v02-vm` directory. It requires an installed QEMU, a Debian 13 amd64
qcow2 OS overlay and four sparse 16 GiB data images. For the initial boot only,
attach a cloud-init seed ISO; detach it after cloud-init has configured SSH so
the strict adapter harness sees exactly five disks. This final start script uses
one vCPU, single-thread TCG and disables TSC to select the HPET clock source.
It forwards only SSH to `127.0.0.1:22282`. No physical block device is attached.
The cloud-init seed must name the guest `nas-v02-vm` and install a temporary
SSH public key. Keep its private key outside this repository.

`install_disposable_omv.sh` follows the official OMV 8 Debian installation sequence
inside that guest, checking the hostname, platform, and dedicated disk serials
before installing packages. Launch it through `systemd-run --unit=nas-v02-install
/root/install_disposable_omv.sh` after copying it into the guest; a background
SSH shell can be terminated during OMV service reconfiguration. It does not
wipe data disks. The guest can reach the
Internet through QEMU user networking; no guest SMB or web listener is forwarded.

Copy the repository/package and `probe_omv_environment.py` into the guest, then
run the probe as root before provisioning. Capture its JSON as baseline evidence.
It requires Debian 13 amd64, installed OMV and MD packages, the separate OS disk,
and four untouched SATA data disks. Its `provisioning_certified` value stays false:
a read-only baseline never certifies creation, permissions, or recovery.

Actual adapter tests must retain the production gate, use an explicit test-only
subclass inside this VM, and execute all pending rows in the dated validation
record. Before destructive tests, save an offline OS/data snapshot or make fresh
copies; record each reset. Never point these helpers at a real NAS.

After installing OMV and the test wheel, `python3 tests/integration/vm_snapshot.py
save` records the complete disposable VM state as `omv-clean`. `restore` rolls
back that guest and all four virtual data images; collect test evidence on the
host before restoring. `list` reports available snapshots. No restore runs
automatically, and the helper only connects to the fixed temporary monitor socket.

`exercise_omv_adapter.py` is the destructive live matrix driver. It accepts
`single`, `raid1`, `raid5`, `raid6`, `lost-format`, `verify` (reboot check),
`existing`, `conflicts`, and `degraded`.
Run only inside the named guest after the clean snapshot, using the installed
test venv. It checks a root-owned disposable marker, exactly five unique
synthetic disk serials, QEMU SATA models and sizes, and OS exclusion before any
provisioning. Restore `omv-clean` between layout/fault cases. Capture stdout and
resource evidence on the host before restoring. Its VM-only adapter subclass
bypasses the release gate; the production adapter remains disabled.
