# NAS Setup v0.2 implementation plan

> **For agentic workers:** Use the implementation and test-driven skills. Independent UI, OMV adapter and VM verification tasks have disjoint files and explicit contracts; review the complete integration before delivery.

**Goal:** Approved bilingual wizard for disks, RAID1/5/6, multiple users, per-folder SMB access and optional applications.
**Architecture:** A pure SetupSelection planner feeds a session-bound consent store and durable operation journal. A fixed OMV adapter provisions only explicitly selected resources; the browser never sends device paths or arbitrary RPC. Legacy app setup remains available internally.
**Tech stack:** existing Python/FastAPI/SQLite, vanilla JS/CSS, OMV8 RPC/MD/EXT4/SMB.
**Spec:** ../specs/2026-10-24-storage-and-sharing-design.md (approved; extend to multiple users and per-folder rw/ro/none).

## Global constraints
OMV8/Debian13/amd64; Portuguese and English; do not modify live user disks during development. System ancestors, ambiguous topology and USB RAID are excluded. A dispatched destructive operation is never blindly repeated. No user passwords in JSON job state or logs. Public v0.1 remains immutable; v0.2 provisioning requires disposable OMV integration validation before enabling production.

## Review focus
- System disk through partitions/MD/LVM is never selectable.
- Expired/replayed consent and changed inventories reject before disk mutation.
- Power loss after format dispatch never causes another format.
- Explicit no-access user cannot access another user's shared folder through POSIX or SMB.
- Language switching/reload must preserve non-secret choices and accurately show RAID synchronization.

### Task 1: planning and contracts (primary)
Create nas_setup/setup_models.py, journal.py, setup_engine.py and tests/test_setup.py, test_journal.py.
- [x] Fail tests for RAID minima/capacity, excluded disks, permissions references, secrets outside selection, replay/expiry/change and uncertain journal operations.
- [x] Implement SetupSelection + plan_setup(selection, inventory); OperationJournal.perform(key, action, reconcile=None, destructive=False); WizardEngine session-bound plan/start/resume with root-private credentials and single shared job lock.
- [x] Run whole Python suite and commit core.

Contract for SetupSelection: apps list (may be empty); storage {mode: existing|single|raid1|raid5|raid6, disks: list[str], volume: str|null}; users list of {name}; shares list of {name, folder: files|media|backups|custom, permissions: {username: rw|ro|none}}; timezone; machine_learning bool. User passwords are a separate dictionary only on start/resume. Plan includes selection, targets (id/model/serial/size/transport), capacity_bytes, tolerance, destructive bool, warnings, steps. Inventory includes disks [{id, model, serial, size_bytes, transport, eligible, reason, signatures}], volumes from original System.inventory, provisioning_enabled bool.

### Task 2: OMV adapter (independent implementation)
Own nas_setup/omv.py, nas_setup/omv_bridge.php, tests/test_omv.py. Do not edit other files.
Interface: OmvProvisioner(system) where system has run(args,timeout,check), inventory(), state. inventory() returns disks and provisioning_enabled. prepare(selection_dict, plan_dict) verifies/install storage prerequisites without formatting. provision(selection_dict, plan_dict, journal, passwords) returns {volume: filesystem UUID, media_path: optional string, shares: [{name,path,permissions}], raid: {state,progress,tolerance}}. status(resources) verifies exact resources and returns RAID status. Uses journal.perform per mutation; credential values never appear in process args. System default new storage provision remains disabled unless an exact validated adapter contract is satisfied; no env-only bypass advertised to users.
- [x] Tests first for topology exclusions, fixed RPC bridge allowlist, no argv credentials, privilege matrix, interrupted format.
- [x] Implement real OMV adapter with schema-checked fixed methods, owner/resource verification, ordinary users and per-share permissions.
- [x] Report meaningful actual command contracts and integration risks; full test suite.

### Task 3: bilingual UI (independent implementation)
Own static/index.html, app.js, style.css, i18n.json, tests/browser-v02.cjs. Use the endpoint contract below.
GET /api/inventory; POST /api/setup/plan body SetupSelection -> plan plus plan_id/expires/confirmation_required. POST /api/setup/start {plan_id, confirmation:'ERASE' or '', erase_disks:[IDs], passwords:{username:password}} -> standard job. POST /api/setup/resume {plan_id?, confirmation?, erase_disks?, passwords?} -> job. GET /api/job -> standard job plus setup:true, resources, selection (no passwords), error. Existing pause/finish/tailscale endpoints remain.
- [x] Browser assertions for disk layout/capacity, multiple users matrix, erase acknowledgements, switches/reload/progress.
- [x] Implement storage -> users/shares -> apps -> review -> progress; allow no optional apps, masked credentials, no storage of passwords in localStorage.
- [x] Keep existing demo login, real-vs-simulated distinctions; version 0.2.0 and translated error states.

### Task 4: integration and runtime (primary)
Modify web.py, auth.py, rpc.py, engine.py, system.py, cli.py/worker.py. Keep v1 test compatibility.
- [x] Add server-owned session IDs, sanitized validation errors, fixed setup RPC operations, shared job serialization.
- [x] Demo fixtures for 4 eligible data disks and protected OS disk, simulated provisioning/shares/RAID transitions.
- [x] Real adapter integration, exact read-only Media bind exception, storage-only jobs and RAID synchronization polling.
- [x] Full Python + browser suites, review regressions.

### Task 5: disposable integration and release
- [x] Try disposable OMV8 VM; validate actual RPC operations on throwaway virtual data disks only, SMB rw/ro/none, all RAID modes and reboot/uncertain operation recovery. Record unavailable infrastructure explicitly; preserve real-provisioning gate if unvalidated.
- [x] Fresh whole-branch security/correctness review; fix important findings with regressions.
- [ ] Versioned 0.2.0 packaging and README, no update to old release assets; deliver browser demo, source and accurate validation limits. Publish v0.2 release only when design's integration gate is met.
