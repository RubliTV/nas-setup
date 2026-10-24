# v0.2 disposable OMV validation

Status: single-disk live case passed; remaining matrix cases pending; **not a provisioning certification**.

The production adapter gate must remain disabled until the actual destructive
paths pass the matrix below. Python mocks, schema inspection, browser simulation,
and an operating-system boot do not establish this result.

## Environment

Attempt started 2026-10-24 on an Apple Silicon (`arm64`) macOS host. No existing
QEMU, Docker, or Lima runtime was available. QEMU 11.1.2 was installed through
Homebrew; the guest is Debian 13.7 `genericcloud-amd64` from the official Debian
cloud-image service. Boot completed in approximately 50 seconds; SSH was responsive
and cloud-init completed successfully. Cross-architecture execution uses QEMU TCG software
emulation, not hardware virtualization.

All guest images, credentials and logs are under `/tmp/nas-v02-vm`, outside the
repository. After initial cloud-init, the seed disk was detached. The VM has a separate virtio OS disk (`NASV02SYSTEM`) and four sparse
16 GiB SATA data images (`NASV02DATA1` through `NASV02DATA4`). Only virtual data
images are eligible for test erasure. QEMU user networking forwards SSH on
`127.0.0.1:22282`; no guest web or SMB port is forwarded to the host/network.
SMB access tests run inside the guest.

Temporary helper scripts:

- `/tmp/nas-v02-vm/start.sh`: start the disposable VM.
- `/tmp/nas-v02-vm/ssh.sh`: connect with its generated temporary SSH key.
- `/tmp/nas-v02-vm/install-omv.sh`: install OMV 8 and its MD plugin in the guest.
- `/tmp/nas-v02-vm/serial.log`: guest boot output.

These temporary paths are local to this validation attempt and are not release
assets. Do not copy the private SSH key into artifacts.

The image SHA-512 matched the published Debian checksum:
`16e0965312c56feb331049d45d2b1b5778f3f942a33d4300eedbb97527707f2bea87c0006f3e4cc7a25fadbff739c4e235a51fb9bcd1b66fd554f7f8e80ba45c`.
The QEMU bottle SHA-256 matched Homebrew metadata before installation.

`lsblk` reports `/dev/sda` through `/dev/sdd` as SATA disks with the four expected
serials. `/dev/vda1` supplies `/`, and `/dev/vda15` supplies `/boot/efi`.

## Recovery observed during setup

The first two-vCPU/multithread TCG run emitted clocksource/RCU stalls and systemd
watchdog failures. The journal then recorded SIGTERM of the SSH session's
installer, apt and dpkg processes, leaving OMV half configured. A later console
state ended with `Failed to fork off sandboxing environment for executing
generators: Protocol error` and `systemd[1]: Freezing execution`. This records
what was observed; a definitive emulator or host-suspend cause was not established.
No data disk had been formatted.

Host memory pressure was not apparent: the host reported 48 GiB RAM and 86% free.
The failed console log and a clone of the OS image were preserved under the
fixed temporary VM directory. A controlled restart with single-thread TCG,
one vCPU, and `-cpu max,tsc=off` succeeded; the guest reports the `hpet`
clocksource and SSH works again. Package recovery runs through the transient
`nas-v02-install` systemd service to avoid SSH session lifecycle termination.
A clean powered-off restart after removing the seed disk also succeeded.

Installed package versions are `openmediavault 8.5.12-1` and
`openmediavault-md 8.1.6-1`; both are fully configured. `dpkg --audit` returned no output,
`openmediavault-engined` is active, and the read-only baseline probe succeeded.
The reviewed NAS Setup 0.2.0 wheel and dependencies are installed in
`/root/nas-venv`; the guest uses HPET. See
`tests/integration/evidence/baseline.json` for package and disk evidence.
All eight installed RPC JSON schemas match the exact pinned adapter hashes.
Installed PHP implementation hashes differ from the audited upstream commit for
DiskMgmt, FileSystemMgmt and MdMgmt, even though their schemas match. The adapter
agent identified absent upstream device-settle waits in this installed version
and added an explicit `udevadm settle` after background completion. This source
comparison is recorded in `tests/integration/evidence/implementation-check.txt`;
live behavior still requires the matrix below.
The latest adapter and PHP bridge were overlaid into the venv, guest PHP lint
and `verify_schemas(raid=True)` passed, and `omv-clean` was saved and listed
on every virtual disk (919 MiB of VM state). This is the reset point for the live
matrix; test output must be copied to the host before a restore.

## Live run findings

The first single-disk attempt completed the actual `filesystem_create`,
`mount_set`, configuration apply, and three user creation calls, then failed at
`share_set` with the bridge's generic `omv_rpc_failed` response. The adapter agent
traced this to OMV's configured new-object UUID sentinel and changed the bridge
to translate the fixed internal `new` marker through OMV's environment setting.
This attempt is **not a passing single-disk result**. Its log is preserved as
`tests/integration/evidence/single-attempt1.log`; no password values appear there.
The harness was updated during the attempt, so traceback source-line snippets
may reflect the newer source; operation names and terminal error are preserved.

The VM was restored to `omv-clean`, then `lsblk` verified all four data disks were
again blank and unmounted before a corrected retry. The OS root and EFI
partitions remained on the separate system disk.

The corrected single-disk attempt passed with actual EXT4 creation, OMV mount,
three ordinary users, two registered shared folders and SMB shares, and the
requested rw/ro/none permission matrices. SMB upload/download content matched;
read-only writes and excluded-user share access were denied. Local POSIX access
was also denied for cross-user paths. Replaying the completed journal invoked
zero mutation RPCs. The final single-disk health was `healthy`, with zero disk
failure tolerance as expected. Evidence is in `single-pass.log` and
`single-resources.json` in the integration evidence directory. Reboot and
remaining RAID/fault cases are separate pending checks.

Existing-volume reuse then passed with zero mutation RPCs. Conflict tests rejected
an existing username, an existing share, and unrelated pending OMV configuration
changes before any provisioning mutation. The test restored its temporary SMB
setting change and applied it. Evidence: `existing-pass.log` and
`conflicts-pass.log`. An actual guest reboot completed with a new boot ID and
both OMV's engine and Samba active; the post-reboot assertions then passed: healthy status, exact SMB file content,
and denied access for the excluded account. See `single-reboot-pass.log`.
The guest was restored to the clean snapshot with all four data disks blank
and unmounted before starting RAID cases.

## Required live matrix

| Check | Evidence required | Result |
|---|---|---|
| Platform | Debian 13, amd64, exact OMV/MD package versions | Passed; OMV 8.5.12-1 / MD 8.1.6-1 |
| RPC schema files | All eight installed schema hashes equal adapter constants | Passed (see `tests/integration/evidence/schema-check.txt`) |
| RPC runtime | Bridge dispatch and actual operation response shapes | Pending |
| Guest disk baseline | Distinct OS root ancestry; four unique unmounted SATA disks | Passed (baseline JSON) |
| Adapter inventory exclusion | Real adapter marks OS excluded and exact four data disks eligible | Pending |
| Single disk | EXT4 create, UUID, registered OMV mount and database objects | Passed |
| Existing-volume reuse | Reuse registered mounted filesystem without mutation RPCs | Passed |
| RAID 1 | Two exact members, synchronization, healthy status | Passed; healthy 100%, tolerance 1 |
| RAID 5 | Three exact members, synchronization, healthy status | Pending |
| RAID 6 | Four exact members, synchronization, healthy status | Pending |
| SMB matrix | Separate users with rw, ro and none; actual SMB write/read/deny | Passed on single disk and RAID 1; RAID 5/6 pending |
| Filesystem ACL | No-access user cannot traverse/read another user's share locally | Passed on single disk and RAID 1; RAID 5/6 pending |
| Reboot | Exact mounts, members, shares, permissions and ownership persist | Passed on single disk; RAID cases pending |
| Degraded array | Missing member detected; protected completion blocked | Pending |
| Conflicts | Existing unrelated username/share and pending OMV changes rejected before mutations | Passed |
| Completed journal replay | Re-entering completed provisioning invokes zero mutation RPCs | Passed on single disk and RAID 1 |
| Interrupted mutation | Lost response/restart never triggers another wipe/format | Pending |

Each storage layout requires a fresh disposable disk set or documented rollback
to a known VM snapshot. Never reclassify a failed operation as successful merely
to continue the matrix. A VM-only test harness may exercise the adapter behind its
release gate, but a harness override is not evidence that the release gate may be
enabled.

## Sources

- [Debian 13 official cloud images](https://cloud.debian.org/images/cloud/trixie/latest/)
- [OMV 8 installation on Debian 13](https://docs.openmediavault.org/en/8.x/installation/on_debian.html)
- [OMV MD plugin](https://docs.openmediavault.org/en/8.x/plugins/available/md.html)
