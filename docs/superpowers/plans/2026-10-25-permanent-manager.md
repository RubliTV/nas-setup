# Permanent NAS Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver the approved persistent manager and beginner-friendly app store.
**Architecture:** Existing unprivileged FastAPI frontend, typed root worker and
SQLite jobs; separate manager module and static UI. Reuse Docker Compose recipes.
**Tech Stack:** Python/FastAPI/SQLite/PyYAML, vanilla JavaScript/CSS, systemd/Docker.
**Spec:** docs/superpowers/specs/2026-10-25-permanent-manager-design.md

## Global constraints
HTTPS 8443, PT/EN, no secrets in browser storage/history/logs; v0.2.x migration;
never erase data or guess external-container recreation. Keep wizard protections.
## Review focus
- Imported Compose host privileges/interpolation/mount escape must fail before writes.
- Restart mid-operation must not look successful or replay destructive changes.
- Login/reset/enrollment cannot bypass authentication or leak passwords.
- Existing systemd supervision must not undo stop/restart requests.
- Docker absent, occupied ports, missing mounts and user permissions must be explained.

### Task 1: Persistent authentication and bootstrap
Files: manager_auth.py, auth.py, cli.py, packaging/install.py; tests/test_manager_auth.py.
Interfaces: ManagerAccount(directory).exists(), create(username,password,replace=False),
verify(username,password); Auth.login_password(username,password,peer), logout(token).
Auth.login(code,peer) retained for initial enrollment/recovery. CLI manager-account
prompts via /dev/tty; reset via explicit root command. Web routes integrated by root.
- [x] Failing tests for salted persistence, bad login/rate limits, reset and enrollment.
- [x] Implement auth and bootstrap; no password in argv or diagnostics.
- [x] Run auth/CLI/packaging tests, review, commit.

### Task 2: Manager worker and app catalog
Files: manager.py, catalog.py, compose_import.py; tests/test_manager.py, test_catalog.py.
Interface: Manager(engine).call(operation,payload). Operations catalog, overview,
options, plan, deploy, job, action, logs. Payloads/results documented in manager.py.
Parent integrates Engine/rpc/web routes. Plan/deploy session-bound token. Manager
uses engine.lock and persisted operations; root worker validates payloads.
- [x] Failing tests for container actions, safe Compose parsing, catalog generation,
  missing variables, mounts/ports, durable jobs, and no secrets.
- [x] Implement real Docker and explicit demo adapters; systemd stack coordination.
- [x] Run backend tests, review, commit.

### Task 3: Guided browser UI and integration
Files: web.py, engine.py, rpc.py, static/manager.js, manager.css, index.html, app.js;
tests/test_manager_web.py, tests/browser-manager.cjs.
Interfaces: /api/manager/{operation}, /api/manager-account, /api/manager-login,
/api/logout; session returns manager_configured, username. Catalog schema from task2.
- [x] Failing HTTP/browser checks for auth, enrollment, app store step navigation,
  Compose variable questions, review/deploy/job flow, existing containers, PT/EN.
- [x] Implement separate login/dashboard/store/details/import screens and real APIs.
- [x] Finish opens dashboard; navigation provides setup recovery without rerunning it.
- [x] Full Python + browser suites, mobile visual review, fix regressions, commit.

### Task 4: Release and validation
Files: version metadata, updater, README, validation document, PROGRESS.
- [x] Update to 0.3.0 with prior versions accepted; preserve manager credentials/jobs.
- [x] Verify update blocks active manager operations as well as wizard work.
- [x] Run all tests, package manifest verification and independent full review.
- [x] Publish RubliTV prerelease, update PR, verify public assets; provide update command.

## Execution ledger
User explicitly authorized implementation; no redundant plan-approval stop.
Tasks 1/2 share no files and define interfaces consumed by task 3. Task 4 consumes
all tasks. Root implements task 3 while independent task workers own 1/2.

Review follow-up: per-project mount guards passed live lifecycle, wrong-identity
refusal/recovery and reboot persistence checks on the final installed runtime.
