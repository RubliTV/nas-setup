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

After the guest installation, create the isolated test environment and marker
inside the guest (the wheel and scripts must already be copied there):

```sh
python3 -m venv /root/nas-venv
/root/nas-venv/bin/pip install /root/nas_setup-0.2.0-py3-none-any.whl
install -m 600 /dev/null /root/NAS_SETUP_DISPOSABLE_VM
python3 /root/probe_omv_environment.py > /root/baseline-probe.json
```

Actual adapter tests must retain the production gate, use an explicit test-only
subclass inside this VM, and execute all pending rows in the dated validation
record. Before destructive tests, save an offline OS/data snapshot or make fresh
copies; record each reset. Never point these helpers at a real NAS.

After installing OMV and the test wheel, `python3 tests/integration/vm_snapshot.py
save` records the complete disposable VM state as `omv-clean`. `restore` rolls
back that guest and all four virtual data images; collect test evidence on the
host before restoring. `list` reports available snapshots. No restore runs
automatically, and the helper only connects to the fixed temporary monitor socket.
A restored snapshot can roll back the guest wall clock. Before any package
update or TLS check, compare host/guest UTC and synchronize the disposable guest
to the host clock. Keep repository signature verification enabled. For this
fixed temporary VM only:

```sh
python3 - <<'PYTIME'
import subprocess, time
subprocess.run(['/tmp/nas-v02-vm/ssh.sh',
                'date -u -s @' + str(int(time.time()))], check=True)
PYTIME
```

`exercise_omv_adapter.py` is the destructive live matrix driver. It accepts
`single`, `raid1`, `raid5`, `raid6`, `lost-format`, `verify` (reboot check),
`existing`, `conflicts`, `degraded`, `finish-degraded`, `syncing`, and
`lost-format-resume`. After a layout passes, `verify` requires an actual guest
reboot with its state retained. `syncing` starts an MD consistency check and
asserts the real engine blocks Finish while synchronization remains active.
`lost-format-resume` must run in a new process after an actual reboot of the
failed-format case, without restoring the clean snapshot first.
Run only inside the named guest after the clean snapshot, using the installed
test venv. It checks a root-owned disposable marker, exactly five unique
synthetic disk serials, QEMU SATA models and sizes, and OS exclusion before any
provisioning. Restore `omv-clean` between layout/fault cases. Capture stdout and
resource evidence on the host before restoring. Its VM-only adapter subclass
bypasses the release gate for the isolated adapter matrix. The production gate
was enabled only after the recorded matrix passed; the packaged smoke uses the
actual production gate without this test subclass.


`packaged_smoke.py ARCHIVE SHA256 --enabled` runs the real packaged bootstrap and
HTTPS API through enabled production provisioning, without the adapter subclass.
Use a clean guest with all four synthetic data disks blank and the MD plugin
absent (`apt-get -y --no-auto-remove remove openmediavault-md`, then confirm dpkg
completion and clean OMV configuration). It targets only data disks 1 and 2 for
RAID 1. Synchronize guest UTC after snapshot restore, upload the checksum-verified
archive and helper, and run the helper through a transient systemd service with
`UMask=0077`. Preserve its sanitized stdout on the host. Bootstrap output containing
the pairing code is captured separately in the guest's root-private
`/root/nas-packaged-smoke/bootstrap-private.log`; never commit or publish that log.
The fixed extraction directory must not exist at the start. Restore the clean
snapshot for another complete attempt after preserving required evidence.

`upgrade_immich_patch.py ARCHIVE SHA256 [--skip-app-test]` validates the packaged
v0.2.2 update against saved jobs and configuration in the identity-marked guest.
`immich_recovery.py` requires an unused Immich project and creates the interrupted
initdb fixture using the pinned PostgreSQL image. It exercises refusal of custom
cluster contents, authenticated recovery, repeated startup, and actual Immich
HTTP readiness for recovered and fresh database directories. It preserves the
recovered directory when testing fresh initialization. Machine learning is disabled.
`immich_backup_ownership.py` checks the real root/filesystem operations with a
PostgreSQL-owned HBA file and a simulated SQL boundary. These helpers must never
run on the user's NAS. Keep raw updater and Compose logs private; only commit the
sanitized PASS output after checking it.
