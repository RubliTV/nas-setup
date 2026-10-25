# Permanent NAS manager and guided app store

The user approved a permanent Portuguese/English manager on HTTPS port 8443,
managing every local Docker container, a tested catalog with Radarr, Sonarr and
Prowlarr, and an advanced Compose importer. Beginner installations use guided
forms and review, never require editing YAML or typing host paths. Continue the
existing project in its dedicated feat/nas-v02 checkout, preserving v0.2 jobs.

## Approaches and decision
Reuse maintained upstream images with our tested Compose recipes and guided
forms. User chose this plus Compose import over importing a large community
catalog. We maintain NAS integration and tested image versions, not image builds.
The UI remains independent of OMV on 8443. OMV owns storage/sharing.

## Behavior
- Setup/update asks for a separate manager username/password via terminal when
  needed; secure web enrollment via pairing is also available for migrated/demo
  installations. Store a salted password hash only, provide local reset, logout,
  CSRF protection, rate limiting, and expiring sessions. Existing pairing works
  for recovery/enrollment without becoming a permanent unauthenticated bypass.
- Finish transitions to dashboard; services remain enabled after reboot.
- Dashboard lists every local Docker container, groups/describes app stacks,
  status, CPU/memory, ports/open links, logs and start/stop/restart. Coordinate
  existing nas-app systemd supervision for installer-owned stacks. External
  containers retain configuration. Arbitrary external recreation/update requires
  their original Compose import; no guessed rebuild from docker inspect.
- App store includes Radarr, Sonarr, Prowlarr. Existing Immich/Jellyfin installations
  appear in dashboard. Tailscale remains a host service with existing setup flow.
- Guided install: choose app, choose mounted data volume and existing sharing
  account/media location, review generated paths/port and install. Defaults chosen
  from discovered NAS data; port conflicts and invalid permissions are explained.
  New config directories are private to the selected app identity; never recursively
  rewrite permissions on a user's media. Consistent media/download root mapping.
- Advanced import: paste/upload Compose, discover missing interpolation values,
  guide through required values, preview services/images/ports/storage, then deploy.
  First version supports documented common Compose service/network/volume options;
  unsupported host privileges, build, mounts outside mounted NAS data, env_file,
  external files, and ambiguous settings fail with actionable errors before changes.
  No host shell commands accepted. Never display secret values in preview/job/logs.
- Manager operations are durable, serialized, asynchronous jobs with named phases,
  persisted terminal outcomes, and interruption marked on restart. Browser closure
  does not cancel them. Updates to owned manager projects preserve volumes and
  require explicit action; deletion of volumes/data is outside this first version.
- Retain existing storage wizard safeguards and 0.2.x state on upgrade. No live NAS
  changes or erasures by development tools. Release v0.3.0 as a prerelease only
  after tests/review. Document what was tested and physical-NAS limits honestly.

## Boundaries
Root worker alone accesses Docker; web runs unprivileged. Authenticated manager is
an administrative account, distinct from SMB users. Validate all worker payloads,
container IDs, Compose inputs, mounts and ports. Secret-bearing requests/files
never enter job history or diagnostic error text. Docker daemon unavailable is
an explained dashboard state, not a broken page. No automatic application API
configuration beyond deployment: app cards explain any first-run setup still
required (including Radarr/Sonarr download-client/indexer connection).
