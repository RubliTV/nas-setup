# v0.2.4 password persistence and visibility

Passwords submitted when installation starts are retained in the existing
root-private job credential file across failed attempts, pause, and worker
restart. Resume combines saved credentials with any genuinely missing values,
without returning passwords in job responses. Completed accounts are verified
through the original operation journal; disk consent and recovery checks are
unchanged. Credentials are removed when storage/user setup succeeds, and startup
cleans orphaned or completed-job files. Atomic credential writes now fsync their
parent directory as well as their contents.

The file uses mode 0600 within the job's 0700 directory. It is not encrypted;
root can read it until cleanup. Invalid, foreign-user, publicly readable, or
symlinked credential files are not reused. Older releases that already deleted
a file require one more entry for unfinished accounts. Before installation
starts, the browser keeps passwords in memory only; reloading that form clears
them. Passwords are never written to browser storage or job history.

All NAS account password inputs, including confirmation and legacy recovery,
have keyboard-accessible Portuguese/English Show/Hide controls. Successful
submission clears revealed as well as masked inputs. Language changes preserve
entered values, and the recovery form remains absent when saved credentials are
available. Existing progress and stale-response protections remain intact.

Validation:
- Python suites pass on 3.12 and 3.15 (195 tests; existing Starlette/httpx warning).
- Regression tests cover failure/pause/interruption plus worker restart, private
  permissions, mixed saved/missing credentials, legacy/corrupt/symlink fallback,
  cleanup after successful storage, and no password in API results or job DB.
- Both Chrome browser suites pass: PT/EN visibility controls, confirmation,
  revealed-password cleanup, no repeat prompt when credentials are available,
  stale poll handling, progress, mobile layout, and existing wizard behavior.
- Mobile screenshot visually checked at 390px; controls remain within inputs.
- Independent review found no material issues. Suggested mixed-credential and
  English-label coverage was added.
- Update fixtures cover installed versions 0.2.0 through 0.2.4.

No new destructive VM or physical NAS run was performed for this patch. Storage
commands, OMV ownership checks, and application images are unchanged. Prior
integration evidence remains in the earlier version validation documents.
