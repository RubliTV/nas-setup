# Implementation ledger
Plan: docs/superpowers/plans/2026-10-24-nas-installer.md
Ruling: Execute inline after the explicit request to build; no further plan approval needed.
Ruling: Use a new dedicated repository in the available chat workspace; configured Dialer directory is absent.
Pre-flight: Core Selection and inventory contracts feed API and engine; frontend consumes only API JSON. Root worker alone owns real system writes.
Task 1 complete: 7 core tests pass after observed failures; durable SQLite transaction prevents competing starts.
Task 2 complete: API tests cover pairing replay, CSRF, origin/host validation, rate limiting and authenticated routes.
Task 3 complete: Real OMV package/Compose/systemd adapter, restricted root worker, bootstrap and release builder implemented. Demo executes no host provisioning commands.
Task 4 complete: Portuguese/English responsive wizard, real API progress, isolated Chrome flow and screenshots verified.
Final review: One fresh independent reviewer found five important issues; all fixed with regression coverage: pairing writes now run as the unprivileged web UID; mount UUID/path associations and persisted binds are guarded; Docker ownership/images/binds and wildcard port conflicts are checked; secrets publish atomically; failures retain bounded redacted diagnostics.
Ruling: This is a new repository without a remote or base branch. Preserve the implementation branch locally; publishing and live NAS installation are outside the current build request. No integration-choice menu is needed to deliver the local artifact.
Limit: No Linux VM runtime or connected NAS is available here. Real OMV package integration, physical J1800 performance, network enrollment and live systemd mount behavior remain explicitly unvalidated.

GitHub distribution: User requested downloads from GitHub and selected personal account nicolasumaras. Add a release-specific HTTPS download helper with an embedded archive checksum, temporary extraction and argument forwarding. Publish v0.1.0 as a prerelease; real OMV validation remains pending. This supersedes the earlier local-only delivery scope.

RubliTV publication: User connected RubliTV and authorized publishing there. README and release helper now target RubliTV/nas-setup; preserve the earlier personal repository.


## v0.2 storage and sharing
User approved the storage design and explicitly requested multiple users with per-folder permissions. This supersedes the earlier apps-only scope. Ruling: proceed with the approved implementation without another permission checkpoint. Disjoint UI, OMV adapter and disposable VM work was delegated under the parallel-agent skill; root integrated the contracts and performed cross-component testing.

Implemented: bilingual storage/users/folders/apps/review/progress wizard, existing EXT4 and single/RAID1/5/6 selection, ordinary users, rw/ro/none permissions, server-owned session-bound erasure plans, secret-isolated credentials, durable operation journal, exact read-only Jellyfin Media exception, versioned packaging.

Independent review identified four issues: recovery plans rejected their own partially provisioned disks; an existing container could retain writable Media; all-read-only ACL masks differed from the checker; completed RAID health could become stale. All four fixes passed fresh independent re-review and regression tests. Recovery review now requests erasure consent only when destructive operations remain. Live validation remains in progress.

Test-driven evidence: core planner/journal/engine/media tests were observed failing before implementation, then passed. Added review regressions were observed failing before fixes. UI agent verified real demo API flows and inspected desktop/mobile screenshots. Real storage remains gated pending the separate disposable OMV validation record. No physical NAS disks were touched.

Integration ruling: prior user authorization to publish to RubliTV covers pushing this implementation branch and creating a draft PR. Keep the public v0.1 release unchanged; no v0.2 install release until the approved live storage matrix passes. The normal repository is retained for continued VM integration work.

Verification snapshot: 101 Python tests pass on Python3.15; Python3.12 passed94 before the final additional regressions. Expanded Chrome/Playwright flow passes; source archive internal/external SHA256 checks and wheel PHP bridge inclusion verified. VM single-vCPU recovery reached OMV service deployment; all8 installed RPC schemas match audited hashes. These are not full live provisioning results.

Live validation update: single disk, existing-volume reuse, RAID1 and RAID5 passed real OMV provisioning, SMB rw/ro/none, POSIX denial, completed-journal replay, actual reboot and degraded completion refusal. RAID6 exposed OMV's 180-second remote receive timeout during configuration deployment; the daemon completed after the client disconnected. Commit81786a5 limits an extended1740-second receive timeout to config_apply, inside Python's1800-second bound. The uncertain-operation journal behavior is unchanged. Actual PHP regression ran red then green, and independent review found no blocker. Fresh full suites pass117 tests on Python3.12 and3.15; browser suite passes. RAID6 retry, lost-format reboot recovery and packaged bootstrap remain pending; production gate stays disabled.

Storage release gate: all approved disposable OMV matrix cases passed, including single/RAID1/5/6, real rw/ro/none and POSIX denials, exact-resource reboot persistence, degraded/syncing Finish blocking, conflicts, existing reuse, completed-journal replay and lost-format response recovery after an actual reboot with zero repeat mutations. Root inspected the evidence and enabled ADAPTER_VALIDATED for the exact checked package/schema contract. Final packaged HTTPS wizard validation remains before publication; it will start with the MD plugin absent and provision a two-disk mirror.

Final package validation: candidate c2301d9eaf7afa14df87b552f4976413e798bcbe25b4576041092330ea9d728b passed the real HTTPS wizard under restrictive root umask0077, starting without MD installed. It installed exact MD8.1.6-1, created a healthy RAID1 mirror plus sharing users/folder, removed temporary credentials, stopped only installer services at Finish while SMB continued, and reopened with fresh authentication and preserved setup. A prior snapshot clock rollback was corrected without bypassing package signatures. The real restrictive-umask bootstrap bug was fixed in ceaed60 with observed failing/passing filesystem+venv regression and independent review. Fresh suites:118 tests on Python3.12 and3.15; unchanged bilingual browser flow passes. Final release rebuild may update docs only and must match all29 tested runtime hashes. Physical J1800 and full optional-app tests remain outstanding.

Publication ruling: publish the validated immutable v0.2.0 prerelease from the reviewed implementation branch under the existing RubliTV publication authorization. Retain the ready PR for review and leave the earlier v0.1.0 release unchanged.
