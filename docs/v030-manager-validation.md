# v0.3 permanent manager validation

## Scope

The manager remains on the existing HTTPS port 8443, with a separate account.
It lists all Docker containers; externally installed containers retain their
original configuration and receive start/stop/restart/log actions. The curated
catalog adds Radarr, Sonarr and Prowlarr. Compose import guides missing variables
and limits host access before deployment. The existing storage wizard remains.

## Automated validation

The final local suites pass **284 tests on Python 3.12 and 3.15**. All three
Chrome browser suites pass. One existing Starlette test-client deprecation
warning remains.

Python regression coverage includes salted manager credentials, rate limits,
session revocation on account reset, origin/CSRF enforcement, create-only
account enrollment, and terminal password input without echo. The updater
accepts v0.2.0–v0.2.4 and v0.3.0, checks both job databases, refuses active work,
and preserves existing state through runtime replacement and rollback.

Manager tests cover session-bound installation plans, all-container pagination,
external actions without inferred recreation, private deployment recovery,
interrupted jobs, failed staging/configuration writes, occupied ports, existing
folders, exact UID/GID access, missing mounts, and bounded/redacted log output.
Compose regressions cover unsupported privileges, symlinks, duplicate YAML keys,
interpolation, private values in public preview fields, oversized expansion,
and mounted-data restrictions. Bind projects accept only continuously running
services with always/unless-stopped restart policies; one-time init jobs and
other policies are rejected before staging. Mount guards check saved identities
before systemd starts the app, and explicit Stop disables its boot startup.
The final live Docker check exposed absent health metadata in containers without
healthchecks. Optional inspect fields now use safe lookups, with regressions for
missing/null metadata and log access. Live stop/start checks also identified normal
Compose exits (130/143) being reported as failed units; systemd now accepts those
clean shutdown statuses. Image versions are explicit release choices.

Chrome/Playwright passes the existing wizard and progress suites, plus the new
manager suite: catalog search, guided settings/review/deploy, manual-container
listing and logs, Compose variable questions, masked values, persistent login,
PT/EN switching, mobile layout, Docker setup, navigation during pending requests,
polling during active operations, and retry after a fresh page load.

Independent code review found stopped polling during active jobs, stale request
navigation, missing retry context after reload, private-plan recovery gaps,
an incorrect recovery-code hint, and bind-project restart semantics. These were fixed with regression coverage. A focused follow-up review of the
final guarded lifecycle found no remaining blockers; its 58 focused tests passed.

## Live validation

See [the live VM record](v030-manager-vm-validation.md) and its sanitized evidence.

Live validation uses only the identity-checked disposable QEMU OMV guest and
synthetic disks. The harness is `tests/integration/manager_smoke.py`; it refuses
unrecognized hosts. Test state is isolated from existing wizard jobs. Results
are recorded separately from demo behavior, which never installs containers.
Final installed-runtime checks passed guarded stop/start/restart for all three
apps, wrong-filesystem-identity refusal and recovery, and durable retry after an
injected pull failure. A real reboot restored Radarr/Sonarr with HTTP 200 while
explicitly stopped Prowlarr remained stopped; starting Prowlarr through the
manager then returned HTTP 200. Existing stopped Immich identities and states
were preserved. The missing-identity test changed isolated test metadata; it did
not unmount or remove a physical disk.

The recipes use LinuxServer images, rather than maintaining application images:

| App | Tested image tag | Default port |
| --- | --- | --- |
| Radarr | `6.4.4.10685-ls323` | 7878 |
| Sonarr | `4.0.20.3014-ls1` | 8989 |
| Prowlarr | `2.6.5.5623-ls165` | 9696 |

## Limits

This remains a test prerelease. Physical J1800 performance is unmeasured.
The VM checks app startup and access; it does not configure user indexers,
download clients, media acquisition, or external accounts. The apps' own
first-run configuration is still required and is explained in their catalog
cards. Arbitrary Compose projects are not certified by the catalog tests.
Updates pull the selected version; they do not silently change pinned versions.
Docker logs are bounded and filtered, but application administrators should
still inspect diagnostics before sharing them publicly.
