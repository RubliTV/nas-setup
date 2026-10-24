# NAS Setup: disks, redundancy and shared folders

Status: approved 2026-10-24, including multiple users with per-folder permissions; implementation and disposable OMV validation complete.
Target: v0.2.0 prerelease in RubliTV/nas-setup. Preserve the published v0.1.0 assets.

## Intent and agreed requirements

Viewers should install the OMV ISO, run one download command, and complete NAS setup in the Portuguese/English wizard. Add disk preparation, redundancy, shared folders, SMB and a file-sharing account. Existing optional applications remain available. All new resources must appear in OMV's own interface.

The user selected broader redundancy support: single disk, RAID 1, RAID 5 and RAID 6, plus reuse of existing mounted volumes. Their J1800 test has two 1 TB data disks; the connection type is not yet confirmed. Recommend RAID 1 for that machine: roughly 1 TB usable before filesystem overhead. A separate OMV system drive is required. RAID 5 needs at least three data disks; RAID 6 needs at least four.

## Approaches considered

1. Single disk and two-disk mirror only: smallest test matrix and simplest onboarding; insufficient for the requested RAID 5/6 support.
2. OMV MD software RAID with EXT4: chosen approach, supporting mirrors and RAID 5/6 through OMV's official MD plugin. Arrays, mounts, users and shares remain manageable in OMV.
3. mergerfs/SnapRAID or ZFS: possible later options; snapshot parity has different protection timing and database requirements, and extra storage stacks expand the recovery and dependency matrix. Not part of this version.

## User-visible workflow

1. **Disks and protection.** Show discovered disks by model, capacity, serial/WWN and connection. Clearly mark the OMV system disk as unavailable. Offer existing mounted storage, a new single-disk volume, RAID 1, RAID 5 or RAID 6. Only offer valid choices for the selected disk count and connection type. Show estimated usable capacity and disk-failure tolerance in plain Portuguese/English.
2. **Shared folders and access.** Default suggestions are Files, Media and Backups, with editable translated share names. Create one or more ordinary file-sharing accounts and ask for each password twice. A per-folder matrix offers read/write, read-only and no access for each account. Authenticated access only. No administrator or SSH privileges. User can turn off shares or finish a storage-only NAS with no optional applications.
3. **Applications.** Optional Immich, Jellyfin and Tailscale. Newly provisioned storage is the default data destination. Immich's database defaults to that same protected volume; selecting a separate unprotected volume explicitly changes the displayed protection summary.
4. **Review.** List each disk by identity, capacity, previous filesystem signatures, operation, array layout, usable capacity, share names and account name. Password is never echoed. Existing-volume reuse shows that no formatting is planned. For a new volume/array, show explicit per-disk erasure acknowledgement plus a typed confirmation. No disk is selected for erasure automatically. Any device or plan change invalidates confirmation.
5. **Install.** Separate durable steps for storage prerequisites, disk preparation, array creation, filesystem creation, OMV mounting, account/shares, application install and readiness. Install/verify the supported openmediavault-md plugin for RAID selections and validate required tools/RPC schemas before any disk wipe, including storage-only jobs. A dependency failure leaves the disks untouched. Report actual RAID synchronization separately from application progress. Do not display 'protected/complete' while initial synchronization is incomplete or the array is degraded.
6. **Ready.** Show Windows \\NAS-IP\Share and macOS smb://NAS-IP/Share addresses, username, app links, RAID state and OMV's storage management link. Include a brief reminder that redundancy does not protect against deletion or ransomware; keep a separate backup. Initial application account wizards and Tailscale authorization still require the viewer.

Suggested share names are editable, independent from stable internal folder paths and persisted when the plan is confirmed. Language switching never renames disks, arrays, users or shares.

## Capacity and hardware rules

- Single disk: one data disk, no disk-failure tolerance.
- RAID 1: exactly two data disks, estimated capacity of the smaller disk, survives one member failure after synchronization.
- RAID 5: three or more data disks, (N - 1) times the smallest member capacity, survives one member failure after synchronization.
- RAID 6: four or more data disks, (N - 2) times the smallest member capacity, survives two member failures after synchronization.
- Estimates exclude filesystem/RAID metadata overhead. Display both familiar advertised TB and useful formatted-capacity estimates without promising an exact number.
- Different sizes require an explicit capacity-loss notice. Never reinterpret RAID 0, spanning or pooling as redundancy.
- First-release RAID creation accepts directly attached SATA/SAS/NVMe drives only. Reject USB-connected RAID members; OMV warns against production USB arrays. Connection uncertainty is a reason to withhold destructive creation, not guess. Existing registered storage can be reused without reconstructing its array.
- Do not claim board port capacity from 'J1800'. Confirm actual connectors before the physical test; two data drives plus the system drive require appropriate connections.

## Disk identity and destructive boundary

The existing mounted-volume-only planner cannot simply be extended to accept /dev/sdX strings. Add a server-owned disk inventory with stable by-id identity, WWN/serial, size, logical/physical sector sizes, parent/child graph, filesystem signatures, read-only/removable/transport flags, mounts and array/LVM membership.

Exclude every physical ancestor backing /, /boot, /boot/efi, swap, the installer/state paths and other system mounts. Exclude active holders, mounted partitions, existing active arrays/LVM/encrypted devices, missing or duplicate identities and incomplete/ambiguous topology. Support the common physical SATA/SAS/NVMe cases first; reject unsupported mappings rather than guessing. Browser-supplied device paths are never execution arguments.

A server-generated plan records selected stable identities and an inventory fingerprint. Confirmation is single-use, expiring, bound to the authenticated session, plan and destructive target list. Before each irreversible step, rediscover identities and recheck exclusions, signatures and ownership. Persist a root-owned operation-intent record before executing the mutation. Hold exclusive worker/job locks; use stable device identifiers accepted by OMV and fail closed on mismatch.

Disk formatting is authorized only by the viewer's specific final confirmation in that NAS session. Developing this feature is not permission to erase the user's real disks. Reusing existing mounted storage never authorizes formatting. Existing recognizable but unmounted filesystems can be identified for the user; automatic import/recovery of arbitrary legacy stacks is outside the first release.

Validation is stage-specific. Before preparation, require the exact reviewed inventory. After an owned wipe, allow only the recorded signature/partition removal. After owned MD creation, allow the exact member identities to acquire the journal-recorded array holder/UUID; this never makes other active arrays eligible. After filesystem creation, accept only the discovered UUID/partition topology tied to that dispatched action. After mount, accept only that filesystem at the recorded OMV mount path. Every stage continues to reject system-disk ancestry, unexpected holders, mounts or device substitutions. The original fingerprint is not compared blindly after our own expected mutations.

No remote arbitrary shell, arbitrary block path, raw configuration upload or unrestricted OMV RPC endpoint is added. The privileged worker exposes only typed, fixed operations.

### Confirmation transport and lifecycle

The browser receives a read-only plan ID and challenge from the worker through the authenticated web API. The web layer attaches a server-derived opaque session ID from its Auth session record to allowlisted worker calls; request bodies cannot supply or override this identity. Worker requests remain restricted to the existing peer UID. Store pending challenges and consumed grants in root-owned transactional state, not browser/localStorage state. The worker matches the plan, session, inventory and selected erasure acknowledgements and consumes confirmation in the same transaction that starts the single job.

A confirmation challenge expires after 15 minutes before consumption. Once consumed, its grant covers only the exact undispatched destructive actions in that immutable plan during that uninterrupted worker execution; initial RAID synchronization may legitimately take hours. Worker restart, failed job, expired/replaced browser pairing session before start, or unexpected plan/inventory changes invalidate unused grants. A normal browser reload during an already authorized running job does not cancel it.

Resume gets a typed payload with job ID and optional new challenge response rather than the current empty-only payload. It reconciles previously dispatched outcomes first. If irreversible actions remain undispatched after failure/restart, the UI presents an updated erasure review and obtains fresh session-bound confirmation. Fresh confirmation cannot override an uncertain dispatched format or adopt a different resource. Purely non-destructive remaining steps can resume after ownership checks without another erasure prompt.

## OMV integration

Use OMV's version-checked RPC services and its normal configuration deployment. Do not hand-edit config.xml, fstab or smb.conf. Check for unrelated pending OMV changes before starting and again before applying; stop if external concurrent changes prevent proving ownership. Do not claim an installer lock excludes administrators using OMV independently.

Read-only source investigation used openmediavault/openmediavault commit e0c7b63bf55064eaa6a5e476800465d27e9b86a5. This is evidence for adapter design, not proof that every OMV 8 point release has identical RPC schemas. Validate supported host versions and schemas in integration tests before enabling provisioning.

Relevant interfaces found:

- DiskMgmt.enumerateDevices/getList and DiskMgmt.wipe; correlate with kernel block topology rather than trusting a single candidate list.
- MD management comes from openmediavault-md. MdMgmt.getCandidates/create exposes mirror, raid5 and raid6 and invokes OMV's normal RAID tooling; it returns the created device information. Persist resulting array identity and exact membership immediately.
- FileSystemMgmt.create is asynchronous and wipes its selected target before creating the filesystem. This method must never be blindly retried. Persist the background task handle and follow Exec status/output to completion.
- FileSystemMgmt.setMountPoint registers the filesystem; re-inventory until the expected UUID and target are actually mounted read/write.
- ShareMgmt.set and setPrivileges define registered shared folders and access. Map filesystem UUID to the OMV mount-object UUID rather than assuming they are the same identifier.
- UserMgmt.setUser creates the ordinary account; SMB.setSettings and setShare enable file sharing and expose selected registered folders. Preserve unrelated service options and existing shares.
- Config.applyChanges/applyChangesBg performs deployment; handle asynchronous results and confirm service readiness, not merely RPC return success.

Exact service names, required schema fields, asynchronous handle formats and method behavior must be taken from the supported installed OMV release, not guessed from method names. Use a narrow root-only RPC bridge that takes sensitive request JSON over stdin/Unix socket rather than exposing passwords in command arguments.

## Shared folders, permissions and app storage

- New shares belong to the selected filesystem and dedicated file-sharing user/group. Validate names and relative paths; reject traversal, symlink escapes, root-directory sharing and conflicts. Do not recursively chmod existing user data.
- Existing unmanaged accounts/shares/paths are reported as conflicts. Never reset an existing user's password or take ownership implicitly.
- Create a new account without sudo, administrator or SSH membership. Both filesystem permissions and SMB access rules must permit the intended account; test write access over SMB.
- No guest write access, root shares, automatic port forwarding or public SMB exposure.
- Shared media is separate from private application state. Jellyfin can read the chosen Media share, while the file-sharing account can upload there. Avoid the existing private .nas-setup parent permissions blocking that access.
- Extend the app bind guard with a root-owned approved-bind manifest for Jellyfin's Media share: filesystem UUID, mount path, OMV shared-folder UUID, exact relative directory and read-only container destination. Revalidate that exact binding and reject symlink escapes at every app start. Do not broadly allow all paths outside .nas-setup.
- Immich's managed uploads, database and secrets stay private. A generic Photos share is not automatically an Immich library, and its database must never be writable through SMB.
- 'Backups' is only a destination share. Do not claim that creating it configures or verifies any backup job.
- Store only the account name in ordinary job state. Keep its pending password in a root-private credential mechanism, pass it through the existing protected channels, redact it from logs/exception/validation output, and erase it after account creation. On uncertain account-creation outcomes, inspect ownership and require credential re-entry where needed rather than silently changing an account. Browser storage never retains the password.

## Durability and retry design

Current Engine reruns every completed step on resume. That is acceptable for some idempotent app operations but unacceptable for wipe/RAID/filesystem creation. Introduce a resource journal with planned, dispatched, completed and uncertain outcomes plus owned resource IDs and source identities.

On resume, inspect disk signatures, array UUID/member list, filesystem UUID, OMV mount object, shared-folder UUID, SMB share UUID and account ownership. Continue only when the exact expected outcome is verified. Never wipe, recreate an array or format again because a response was lost. If a destructive operation may have started and its completion cannot be established, stop with a recovery-required message. A reboot cannot revive an expired erasure authorization to start a new destructive action. Pending actions that were never dispatched require a fresh confirmation.

Pause remains between safe steps. During formatting or RAID setup, show that pause is pending; do not kill storage tools. RAID synchronization is separately monitored. Reboots are never automatic. Share/app readiness can be reported while synchronization proceeds, but protected completion and the Finish action wait for a healthy fully synchronized array. A missing/degraded member produces a visible warning and blocks new destructive setup.

Persisted app mount guards continue to verify both UUID and path. Journal ownership covers the generated OMV objects and intended directories. Uninstall, rollback, array resizing, level conversion, member replacement and data migration are not automated by this first release; OMV remains their administration interface.

## Validation and delivery

1. Planner/property tests: RAID disk minima, capacity, mixed sizes, unique identities, OS ancestry, mounted/held disks, USB RAID refusal, empty app selections and shares-only setups.
2. API tests: authenticated/CSRF boundaries; no supplied block paths; confirmations expire, cannot be replayed, and fail when inventory changes; no password in response/job/log/validation errors.
3. Fault-injection tests at every boundary around wipe, MD create, format, mount, user/share create and apply. Demonstrate that interruptions never repeat destructive operations and never adopt an unrelated resource.
4. Bilingual browser flows for existing volume, single disk, mirror, RAID 5 and RAID 6; capacity updates, erasure review, password validation, language changes, progress/reload and storage-only setup. Demo fixtures must explicitly simulate enough disks for each layout.
5. Disposable OMV 8 VM with separate OS and virtual data disks: verify actual RPC schemas, mount/database integration, SMB read/write/deny behavior, array synchronization, reboot persistence, each allowed RAID level, degraded detection, ownership conflicts and interrupted recovery. Loop devices/mocks alone do not prove system-disk exclusion or live OMV behavior.
6. J1800 test with the user's two 1 TB drives only after identifying their connection type, backing up any existing data and explicit on-device erasure confirmation. Validate mirror reboot/read/write behavior and application coexistence.
7. Publish a new immutable v0.2.0 prerelease only after the corresponding destructive paths pass disposable-VM integration. If that environment is unavailable, deliver source and an explicitly simulated demo, keeping real provisioning disabled until verified. Do not label mocked tests as disk-provisioning validation.

## Remaining decisions and defaults

User decisions already supplied: RAID 5/6 must be included; test rig has two 1 TB data drives. Proposed implementation defaults: MD+EXT4, RAID 1 recommended for that rig, authenticated SMB, multiple ordinary file-sharing accounts with per-folder permissions, Files/Media/Backups suggestions, direct-attached RAID only, no migration or repair automation. User approved implementation and the multiple-user extension. Confirm SATA/SAS versus USB before the physical test; it does not block implementation against disposable virtual disks.

## Builder review resolutions

An independent design check identified four implementation gaps, now specified above: stage-aware disk topology transitions, session-bound confirmation transport/atomic consumption/recovery, installing storage prerequisites before destructive work, and exact approved external Media binds in application mount guards. No new product choice was needed to resolve them.

## Sources

- https://docs.openmediavault.org/en/8.x/plugins/available/md.html
- https://docs.openmediavault.org/en/stable/administration/storage/raid.html
- https://docs.openmediavault.org/en/stable/administration/storage/sharedfolders.html
- https://github.com/openmediavault/openmediavault/tree/e0c7b63bf55064eaa6a5e476800465d27e9b86a5/deb
