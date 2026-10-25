# v0.2.1 recovery validation — 2026-10-24

## Report and confirmed defect

The user's job stopped with `omv_configuration_changed` after prerequisites,
with an empty operation journal, no OMV checkpoint, and `Config.isDirty([])` true.
The old engine discarded the session grant on failure; retry generated another
plan and sent the viewer back through summary and ERASE without addressing the
pending configuration. That flow is fixed.

The user's pending module names and their origin were not supplied. An initial
source interpretation incorrectly attributed this to the MD plugin's first
install. Live testing disproved that: a clean v0.2.0 installation proceeded into
storage. `dpkg --compare-versions "" lt-nl 7.1.1` returns false; the postinst dirty
branches apply to upgrades from older nonempty versions. This interpretation
was corrected before publication. We do not claim to have reproduced the
original cause on the user's hardware.

## Environment and controlled failure

Reused the identity-checked disposable Debian 13 amd64 / OMV 8.5.12-1 /
openmediavault-md 8.1.6-1 QEMU guest described in `v02-vm-validation.md`.
Only the separate synthetic OS disk and four synthetic 16 GiB SATA images were
attached. No physical NAS disks or user credentials were accessed.

`tests/integration/upgrade_recovery.py` installs published v0.2.0, SHA-256
`8a7e4fb361ce5b800f2354f73c7b6a35ce36880975e9f44a734ad7f78ca73cf2`.
A clearly marked test-only hook uses OMV's real `omv_module_set_dirty` helper to
mark `mdadm` and `fstab` after prerequisites. This deliberately injected condition
reproduces the reported failure boundary: failed storage step, empty journal,
no checkpoint. It does not establish why the user's configuration was pending.
The MD plugin is already installed in this fixture; an inherited harness log
label saying “installs missing MD plugin” does not assert missing-plugin coverage
for this run. That label is corrected in the checked-in harness.

The candidate archive SHA-256 is
`3f5a8bbab57ff6b1fd6c3c037d13b16258ea16070267f46d920b1fe2ec8b11fe`.
The test invokes the real packaged `--update`, verifies the saved failed job and
certificate/configuration remain, authenticates over verified HTTPS, obtains a
pending-configuration offer, and resumes with passwords plus that explicit offer.
No new disk plan or ERASE confirmation is submitted.

## Final runtime and checks

The final runtime candidate SHA-256 is
`63350a5ff84e5e37181f5a5c37a6fd719e66d40b50a087535b612a45de6709a0`.
It adds the reviewed post-deployment digest guard, corrects the sole-mdadm panel
for non-RAID jobs, and labels pending services in Portuguese/English.
`tests/integration/verify_patch_runtime.py` checks a second patch update against
the completed job, verified HTTPS, unchanged NAS configuration/certificate and
array health, plus automatic mdadm-only repair using another explicit marker.

The full unit suite passes 163 tests on Python 3.12 and 3.15, with one existing
Starlette/httpx deprecation warning. Chrome/Playwright passes bilingual/mobile
flows, recovery without review redirection, per-session pending-change approval,
single-disk mdadm recovery, and password cleanup. Independent reviews covered
core recovery, updater locking/rollback, and both pending-panel follow-up fixes.

Both live checks passed. Sanitized evidence:

- `tests/integration/evidence/v021-upgrade-recovery.log`: injected old-release
  failure, preserved failed job and TLS/config, inline pending approval without
  another ERASE/plan, completed healthy RAID1/users/share, credential cleanup,
  Finish preserving SMB, and authenticated reopen.
- `tests/integration/evidence/v021-final-runtime.log`: final runtime update with
  identical completed job and NAS configuration/certificate, verified HTTPS,
  healthy RAID1, and automatic mdadm-only repair preserving XML and array health.

These are storage-only tests. The optional applications and physical J1800
performance were not retested in this patch. The original unknown pending-module
cause on the user's machine remains unconfirmed; recovery now exposes the actual
pending services within the wizard instead of assuming their origin.
