# Phase 6 — Dashboard Merge

## Tasks
- [ ] `app/templates/dashboard.html` — add MadTracked stat summary card at top:
      Queued / Pending / Copied / Finished / Error counts polled via HTMX every 30s
- [ ] Add "Poll Now" button on the unified dashboard that POSTs to `/api/tracker/poll`
- [ ] Keep existing Librarian scan section (source picker, last scan card, Scan button) below the tracker card
- [ ] Tracker card shows first-run banner when applicable (Alpine.js conditional)
- [ ] Tracker card shows "Approve All & Start" button when queued backlog items exist
