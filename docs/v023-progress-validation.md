# v0.2.3 progress and credential recovery

The old percentage counted completed steps. With eight steps, it jumped about
12–13 points while downloads and startup exposed no intermediate operation.
The UI rebuilt step rows on every poll, restarting their animations. The resume
form used the whole storage-step status rather than completed account operations.
An in-flight poll could also overwrite a successful resume with an older failed
snapshot.

This patch keeps the completed-step percentage honest and animates its width.
A separate persistent activity strip displays allowlisted operation names and
elapsed step time, with no estimated completion time or synthetic percentage.
The connected label means status requests are succeeding, not proof of command
throughput. A delayed/failed connection stops both activity animations. Reduced
motion disables motion, while the textual status remains available. Older saved
jobs without start timestamps omit elapsed time until a new step begins.

The backend now reports the accounts without durable successful creation records.
Resume accepts passwords for only those accounts, discards surplus known-account
credentials from older clients, and still runs the existing OMV ownership/UID
verification before reusing users. A dispatched operation with no completed
result is not considered a finished account. Unexpected recovery still fails
closed. Accepted credentials are removed from the DOM; action epochs invalidate
older poll responses. Passwords are not saved in progress metadata or logs.

Validation:
- 184 Python tests pass on 3.12 and 3.15, with the existing Starlette/httpx warning.
- Chrome/Playwright existing bilingual/mobile wizard suite passes.
- New browser regression passes: current operation, honest percentage, persistent
  animated row, elapsed time, per-account prompts, stale response after resume,
  credential cleanup, language switching, network failure, reduced motion,
  mobile layout and older saved jobs.
- Real JobStore/scoped callback test checks live activity while an installation
  step is still blocked. Account reuse test checks original owner/UID without
  sending a password or resetting the user.
- Independent code review found no material issues. Its minor observation about
  the old step icon pulsing offline was fixed and covered by the browser check.
- Updater tests include 0.2.0, 0.2.1, 0.2.2 and 0.2.3 installed versions.

No new physical NAS or destructive VM provisioning run was needed for this patch.
Storage commands and the pinned application images are unchanged. Existing live
storage/Immich evidence remains in the v0.2/v0.2.1/v0.2.2 validation documents.
