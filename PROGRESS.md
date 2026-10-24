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

Independent review identified four issues: recovery plans rejected their own partially provisioned disks; an existing container could retain writable Media; all-read-only ACL masks differed from the checker; completed RAID health could become stale. Fixes include regression coverage; final review and live validation remain in progress.

Test-driven evidence: core planner/journal/engine/media tests were observed failing before implementation, then passed. Added review regressions were observed failing before fixes. UI agent verified real demo API flows and inspected desktop/mobile screenshots. Real storage remains gated pending the separate disposable OMV validation record. No physical NAS disks were touched.
