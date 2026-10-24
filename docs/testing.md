# Validation record

Automated tests exercise the real planner, SQLite job store, authentication/API, secret generation and Compose generation. A fake system adapter is used when a test would otherwise require root, apt, Docker, or hardware. The browser test exercises the full demo API and UI.

## Required before recommending the public installer

Use a disposable **OMV 8 / Debian 13 / amd64** VM, with separate system and data disks:

- Install the packaged release and verify HTTPS certificate, single-use login, Portuguese/English switching.
- Run Tailscale-only setup and authenticate with an authorized account. Check access from a second device.
- Run Immich with and without machine learning, and Jellyfin; verify first-run account setup and actual media access.
- Confirm package install, OMV-Extras repository/RPC behavior, and the OMV Compose plugin on the supported versions.
- Confirm all bundled images pull and execute on the target CPU; the J1800 needs a physical test. Disabling ML does not prove every other image supports that CPU.
- Repeat an interrupted installation and verify secrets and uploaded media are retained.
- Reboot with the data disk present, then absent; verify no app starts against an OS-disk fallback directory.
- Check a changed filesystem UUID, unavailable database disk, full volume, occupied port, package-manager lock, and existing unmanaged stack.
- Confirm Finish disables only the installer; `sudo nas-setup reopen` restores it.
- Inspect file permissions, service ownership, API origin/CSRF behavior and logs for secrets.

Live storage validation is tracked separately in [v02-vm-validation.md](v02-vm-validation.md). The packaged installer also passed the real HTTPS wizard on a fresh disposable OMV VM: installing the initially absent MD plugin, creating RAID 1/users/a share, protecting credentials, stopping only the installer at Finish, and reopening with preserved state. Tailscale enrollment, the full media application combination, and physical J1800 validation remain separate requirements. A green local suite is not a claim of hardware or deployment readiness.

## Local results (2026-10-24)

The v0.2 Python suite covers RAID minima/capacity, excluded/changed disks, strict users and folder permissions, session-bound erasure consent, expiry/replay, journal-aware recovery, uncertain mutation refusal, SMB/ACL contracts, and exact read-only Media binds. Chrome/Playwright exercises storage modes, multiple users and permissions, Portuguese/English switching, reload, password handling, erasure consent, synchronization, completion and mobile layout. Fresh full suites pass 118 tests on Python 3.12 and 3.15. One Starlette test-client deprecation warning remains.

Security/recovery regression tests also cover pairing temp-file symlinks, a moved filesystem UUID, unmanaged Docker projects, changed bind sources/access mode, occupied ports, invalid saved secrets and redacted failure diagnostics. These are mocked or local filesystem/socket tests; they do not establish live OMV compatibility.

Run `python -m pytest -q` and `node tests/browser.cjs` for current counts and results. The manual live VM harness is under `tests/integration/`; it must only target disposable virtual disks.
