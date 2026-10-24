# NAS installer Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task. Steps use checkbox syntax.

**Goal:** A Portuguese/English NAS-hosted installer with a real privileged installation worker and a harmless local demo.
**Architecture:** Static browser UI talks to authenticated FastAPI; a root worker executes allowlisted jobs through a restricted Unix socket. Durable job state and mounted-volume validation gate all installation writes.
**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, standard-library worker, plain JavaScript/CSS, systemd, Docker Compose.
**Spec:** ../specs/2026-10-24-nas-installer.md

## Global Constraints
OMV 8 / Debian 13 / amd64. Portuguese default, English toggle. No disk formatting or share administration. Apps optional. No arbitrary shell commands. No fabricated public installation URL. J1800 performance requires physical testing.

## Review Focus
- Missing or replaced data filesystem: stop before writes and container startup (Task 1/3).
- Authentication replay and CSRF: refuse requests with no authenticated session or invalid origin/token (Task 2).
- Interrupted installation: inspect reality and retain secrets on retry (Task 3).
- Existing unrelated apps and port listeners: refuse replacement (Task 3).
- Translation switching during installation: keep state and show translated progress (Task 4).

### Task 1: Contracts, storage, durable jobs
Files: nas_setup/models.py, storage.py, jobs.py; tests/test_core.py.
Interfaces: Selection validates apps/volume IDs; make_plan(selection, inventory) -> dict; JobStore persists one active job and supports interrupted recovery.
- [ ] Write tests for missing mount, unsafe paths, unsupported CPU/OS, concurrency, and recovery; run pytest tests/test_core.py and observe failure.
- [ ] Implement contracts and durable state; rerun until passing.
- [ ] Commit tested core.

### Task 2: Authenticated API and worker transport
Files: nas_setup/web.py, auth.py, rpc.py; tests/test_api.py.
Interfaces: create_app(backend, state_dir, origin, demo=False); backend.call(operation, payload) -> dict; sessions carry CSRF tokens.
- [ ] Write API tests for auth, replay, CSRF/origin validation and plan/start boundaries; observe failure.
- [ ] Implement auth and restricted local socket protocol; run whole pytest suite.
- [ ] Commit API.

### Task 3: Installer, bootstrap, packaging
Files: nas_setup/engine.py, system.py, worker.py, cli.py, data/; scripts/bootstrap.sh, build_release.py; tests/test_engine.py.
Interfaces: Engine.call handles inventory/plan/start/job/resume/pause/tailscale/finish; System adapter performs checked subprocess operations; DemoSystem provides explicit simulation.
- [ ] Write tests for preserved secrets, mount loss, existing app conflicts and injection boundaries; observe failure.
- [ ] Implement real package/app setup and container lifecycle guards, durable execution, pause/recovery and demo adapters.
- [ ] Implement Linux bootstrap and checksummed release packaging; test unsupported-host refusal.
- [ ] Run suite and commit.

### Task 4: Bilingual UI and delivery verification
Files: nas_setup/static/{index.html,app.js,style.css,i18n.json}; tests/browser.cjs; README.md; docs/testing.md.
- [ ] Write browser flows for login, language switching, storage/app selection, plan confirmation and simulated installation.
- [ ] Implement responsive UI; run browser checks and inspect screenshots.
- [ ] Build distribution, review changes with a fresh reviewer, fix important findings, run complete suite.
- [ ] Document exact commands and live-OMV validation limits, commit, and open demo for the user.
