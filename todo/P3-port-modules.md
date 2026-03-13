# Phase 3 — Port MadTracked Modules (additive, no Librarian files touched)

Port each file from `ref/app/` verbatim. Adjust only the DB path string
(`madtracked.db` → `librarian.db`) where it appears inside these files.

## Tasks
- [ ] `app/scheduler.py` — port from ref (APScheduler poll loop, first-run logic, quota, semaphore)
- [ ] `app/watcher.py` — port from ref (watchdog share monitor, marks copied→finished on delete)
- [ ] `app/copier.py` — port from ref (chunked copy, subtitle detection, quota helpers)
- [ ] `app/copy_progress.py` — port from ref (in-memory progress tracker for SSE)
- [ ] `app/notifier.py` — port from ref (ntfy.sh fire-and-forget notifications)
- [ ] `app/version.py` — port from ref (single VERSION constant)
