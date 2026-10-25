# Manager v0.3 disposable VM validation

Validation ran on the existing `nas-v02-vm` OMV 8 / Debian 13 amd64 guest on
2026-10-25. Docker reported 29.9.2. This is a disposable QEMU VM result, not a
physical NAS result. The guest used one emulated CPU and 4 GiB RAM.

Every live script requires Linux root, the exact VM hostname, a root-owned
non-symlink marker at `/root/NAS_SETUP_DISPOSABLE_VM`, the five exact synthetic
disk serials, and four 16 GiB QEMU SATA data disks. The scripts refuse execution
on the development Mac. No disks were formatted or unmounted for these tests.

## Maintained recipes

The production `Manager` and `System` classes deployed the following recipes into
isolated worker state `/root/nas-v030-manager-test`. Storage and sharing used the
already mounted NAS volume, existing `SmokeFiles` share, and existing `smokeana`
account (UID 1000, GID 100).

| Application | Image tag | HTTP port | Result |
|---|---|---:|---|
| Radarr | `6.4.4.10685-ls323` | 7878 | HTTP 200; actual app UID/GID 1000:100 |
| Sonarr | `4.0.20.3014-ls1` | 8989 | HTTP 200; actual app UID/GID 1000:100 |
| Prowlarr | `2.6.5.5623-ls165` | 9696 | HTTP 200; actual app UID/GID 1000:100 |

All images came from `lscr.io/linuxserver`. Each deployment reached a persisted
`succeeded` job. Config directories remained mode 0700 with the selected
ownership; private Compose files were mode 0600. Container commands under the
selected UID/GID confirmed write access to `/config`, and to `/data` for Radarr
and Sonarr. The test also checked the actual application process identity using
`docker top`.

A separate manually created container successfully completed real manager stop,
start, restart and log-read operations. Its unsupported update/recreation request
was rejected. OMV configuration XML, existing share ownership/mode, and all three
prior Immich container identities were preserved. Prior Immich containers were
already stopped before this run.

The first attempt stopped at a test harness error: Docker requires a PID column
in custom `docker top` output. The corrected harness retained the already
successful Radarr deployment and completed all checks. The raw sanitized log
keeps that failed harness attempt rather than concealing it.

Evidence:

- `tests/integration/manager_smoke.py`
- `tests/integration/evidence/v030-manager-smoke.json`
- `tests/integration/evidence/v030-manager-smoke.log`
- `tests/integration/evidence/v030-manager-recipe-runtime-sha256.json`
- `tests/integration/evidence/v030-catalog-image-digests.json`

## Final guard and retry verification

The initial recipe smoke used a candidate source snapshot. Final lifecycle tests
load the package copied into the VM's real `/opt/nas-setup/venv` installation,
including the backend guard commit `e18adb7` Docker metadata fix `5577d54` and clean-stop status fix `2d04288`.
The generated units use that installed interpreter/module, without a candidate
`PYTHONPATH` or systemd environment override. This refresh is an integration test;
it is not a claim that a published v0.3 installer/update ran in this VM.

The first final-runtime attempt reproduced a Docker 29 compatibility error:
`.State.Health` is absent on images without a health check, and direct template
access failed. That caused actions to fail and the overview to skip those
containers. Safe optional-field lookups fixed the error. The raw guarded/retry
logs retain the failed attempts. Final checks include a genuinely unlabeled
container built from a tiny local empty image and log reads on both that fixture
and the real external LinuxServer-based container.

A second live finding was that normal Compose stop returned exit status 130.
Containers stopped and units were disabled correctly, but systemd labeled the
unit failed. The final unit declares clean interruption statuses 130/143. Only
the exact previous unpublished Radarr fixture unit was updated after byte
comparison; production mismatched-unit refusal remains in place. The first
fixture rewrite attempted the production exclusive-create helper and was
rejected without changing the file. The harness then used a mode-0600 temporary
file, fsync and atomic replacement after the same exact comparison.

The injected pull failure and durable retry passed: the original failed job
remained unchanged, a distinct successful retry persisted, and its real container
was running. This was repeated from a fresh isolated state on the exact final
installed runtime.

All three final installed-runtime projects passed migration to mount-aware
systemd supervision, stop (inactive and disabled), start, real restart (changed
Docker `StartedAt`), and HTTP 200. Docker's restart policy was `no` in every
supervised container. Replacing only the expected UUID in the isolated manager
database kept Radarr stopped; restoring the original record allowed startup and
HTTP 200. No filesystem was unmounted or disk detached. Immutable private
Compose files remained consistent with their approved staged plans.

After the successful identity refusal, systemd had already unloaded the stopped
unit, so the test's `reset-failed` cleanup returned 1. The harness was corrected
to tolerate that cleanup result and resumed restoration only after validating
all three logged lifecycle passes and the identity-refusal pass. Its final
result was `MANAGER_GUARDED_SMOKE_PASS` with process exit 0.

One real VM reboot changed the boot ID from
`61fc27d6-61e3-42d1-ac59-6de484bdcf0f` to
`e146236b-88e9-4398-9f38-f2fbf61dd81b`. Radarr and Sonarr automatically returned
with enabled, active units and HTTP 200. Prowlarr, explicitly stopped through
Manager before reboot, remained exited with its unit disabled. Its post-reboot
Manager Start then returned it to an enabled, active unit and HTTP 200. All
three prior Immich container identities remained stopped with Docker restart
policy `no`, and their application unit had no enabled boot entry. The final
reboot script completed with `MANAGER_REBOOT_SMOKE_PASS` and process exit 0.
The VM was asked to power off after all evidence was collected; shutdown
confirmation is recorded in the VM control log. The
scripts are `manager_guarded_smoke.py`, `manager_retry_smoke.py` and
`manager_reboot_smoke.py`. Final source hashes are recorded separately from the
initial recipe source hashes.

Final evidence:

- `tests/integration/evidence/v030-manager-guarded.json` and `.log`
- `tests/integration/evidence/v030-manager-retry.json` and `.log`
- `tests/integration/evidence/v030-manager-reboot.json` and `.log`
- `tests/integration/evidence/v030-manager-units.txt`
- `tests/integration/evidence/v030-manager-final-runtime-sha256.json`
- `tests/integration/evidence/v030-manager-vm-control.log`

## Execution and evidence

The only SSH transport was `/tmp/nas-v02-vm/ssh.sh`, pinned to
`root@127.0.0.1:22282` and the existing disposable VM host key. Each live script
runs `guard()` before mutations. Scripts ran asynchronously through
`systemd-run` with the installed `/opt/nas-setup/venv/bin/python`, writing
sanitized logs under `/root/nas-v030-manager-*.log`. Job terminal records contain
operation, ID, status and error code, never command output containing secrets.
The integration scripts contain the actual Docker/systemd commands and explicit
assertions used by each check.

## Scope

These checks establish container startup, initial application HTTP availability,
identity/permissions, and the manager operations explicitly listed above. They
do not configure external indexers or download clients, test application account
creation or media acquisition, or establish physical NAS compatibility. Secret
values, API keys and application configuration contents are excluded from the
published evidence.
