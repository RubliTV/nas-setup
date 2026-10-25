# v0.2.2 Immich initialization recovery

The first-start logs supplied on 2026-10-24 showed PostgreSQL running initdb at
00:27:16 UTC, Immich failing with ECONNREFUSED at 00:27:20, and initdb still syncing
files when Compose stopped the stack. The next database start skipped setup
because PG_VERSION existed. The `immich` database and host authentication rule
had never been created. Increasing the HTTP timeout alone cannot repair this.

## Change

The guarded stack runner starts database/cache dependencies separately and waits
for the final PostgreSQL process and a successful SQL query before launching the
server. It does not confuse initdb's temporary socket-only database with the
finished server. New compositions also require healthy database/cache services.
The installation readiness window permits database initialization plus server
startup (15 minutes for Immich).

For the reported interrupted state, recovery is additive: a password-protected
rule limited to the `immich` database, `postgres` role and directly connected
networks is added, and the database is restarted before the missing application database is created.
The restart precedes CREATE so an interrupted attempt naturally re-enters recovery. The original
HBA file is preserved as `pg_hba.conf.nas-setup-before-recovery`; its directory is
inside the managed database bind, below the root-private `.nas-setup` namespace.
The complete backup is fsynced and published with an exclusive hard link before
HBA replacement; it retains PostgreSQL ownership and mode 0600.
The password and existing PostgreSQL files are retained. No disk operation,
cluster reset, database deletion or photo deletion is performed.

Recovery refuses extra databases, custom roles, user relations in postgres or
template1, a missing password, unexpected HBA location, or symlinked HBA/backup.
Existing Immich databases are never repaired automatically. A TCP SQL probe
using the stored credential must pass before the server starts. The same path
handles saved v0.2.1 compositions after updating the runtime.

The first recovery candidate exposed a pinned-extension behavior during actual
Immich migrations: `pg_reload_conf()` sends SIGHUP, and pgvecto.rs 0.2.0 exits its
background worker with status zero. PostgreSQL unregisters that worker; later
vector operations fail with broken IPC. Recovery therefore restarts PostgreSQL
instead of reloading it, then waits for final-process SQL readiness again.
Sources: [pgvecto.rs v0.2.0 worker](https://github.com/tensorchord/pgvecto.rs/blob/v0.2.0/src/bgworker/normal.rs)
and [PostgreSQL background-worker exit handling](https://www.postgresql.org/docs/14/bgworker.html).

## Verification

Local regression tests cover dependency gating, final-server readiness order,
pristine-cluster recovery, preserved files/backup, repeat recovery, unexpected
cluster refusal and template1 checks. Existing updater tests now exercise
0.2.0, 0.2.1 and 0.2.2 input versions. Chrome/Playwright checks the Portuguese and
English wizard, retry flow, credentials, permissions and mobile layout.

`tests/integration/upgrade_immich_patch.py` and `immich_recovery.py` are restricted
to the identity-marked disposable OMV guest. They test the packaged update,
unchanged saved job/configuration/certificate, the pinned PostgreSQL image with
initialization deliberately stopped after initdb, recovery refusal with custom
roles/schemas/template tables, authenticated TCP recovery, and actual Immich HTTP
readiness after recovery and after a separate fresh initialization.

The complete unit suite passes 177 tests on Python 3.12 and 3.15, with one
existing Starlette/httpx deprecation warning. Chrome/Playwright passes bilingual
and mobile wizard flows. Independent review found and verified fixes for SQL
prefix matching, template1 coverage, final-process ordering, HBA backup ownership,
the extension reload behavior, and atomic backup publication.

The database sequence candidate archive is SHA-256
`9d11a6525a7b7626408ddaa5149bd5f49947f447c0f6caf4b9765f400c4285c1`.
The final runtime candidate is
`65a9f611d2cfe8271b5ac74da90b5783f8a5763b0dd2a8db589704948f84ff68`;
its only runtime change from the database sequence candidate is atomic HBA backup
publication and rejection of an inconsistent existing backup. Root/UID filesystem
checks exercise that exact code. Earlier unpublished candidates exposed the
reload/worker and backup-ownership issues; neither candidate was released.

The database sequence run passed: packaged update preserved jobs/configuration/
certificate, custom cluster contents were refused without HBA changes, the
original HBA and PG_VERSION were preserved, the wrong password was refused,
repeat recovery was idempotent, and both recovered and fresh Immich startup
reached HTTP readiness. The final runtime update and ownership check are recorded
separately to distinguish the atomic-backup delta from the full image run.

Live result logs and final release/runtime identity are stored under
`tests/integration/evidence/v022-*`. Private Compose and updater logs remain in
the disposable guest; they are not distributed with the release.

Physical J1800 timing, machine-learning workloads, uploads/transcoding and the
combined Jellyfin/Tailscale stack are outside this regression. The guest uses
QEMU CPU emulation, not the user's NAS. Unit tests are not hardware validation.
