# Phase 5 — UI Layer

## Tasks

### New templates (tracker section)
- [ ] `app/templates/tracker_dashboard.html` — stat cards (queued/pending/copied/finished/error),
      first-run banner, Poll Now button, recent activity table, share usage progress bars
- [ ] `app/templates/tracker_items.html` — table of all TrackedItems with status badges;
      Approve/Skip (queued), Re-copy (copied), Reset/Retry (finished/error);
      Alpine.js status filter bar; backlog badge on queued rows
- [ ] `app/templates/tracker_logs.html` — SSE live copy log (can share with existing logs.html or be separate)

### settings.html — tabbed layout
- [ ] Restructure into three Alpine.js tabs: Librarian | Tracker | Notifications
- [ ] Librarian tab: existing Radarr/Sonarr URL/key/root/format + Test Connection (unchanged)
- [ ] Tracker tab: tags, poll interval, require approval, max concurrent copies, max share size GB,
      max share files, share path
- [ ] Notifications tab: ntfy URL, ntfy topic, ntfy token, per-event toggles (copied/error/finished/first-run)

### New routes — `app/routers/tracker_ui.py` (new file)
- [ ] `GET /tracker` → tracker_dashboard.html
- [ ] `GET /tracker/items` → tracker_items.html
- [ ] `GET /tracker/logs` → tracker_logs.html (SSE consumer page)

### New routes — `app/routers/tracker_api.py` (new file)
- [ ] `POST /api/tracker/poll` — trigger manual poll
- [ ] `GET /api/tracker/poll/status` — is poll running?
- [ ] `POST /api/tracker/items/{id}/approve` — queued → pending
- [ ] `POST /api/tracker/items/{id}/skip` — queued → finished (skip permanently)
- [ ] `POST /api/tracker/items/{id}/reset` — finished/copied/error → pending
- [ ] `GET /api/tracker/items` — list all TrackedItems JSON
- [ ] `GET /api/tracker/quota` — return quota usage stats
- [ ] `GET /api/tracker/sse` — SSE stream for live copy log
- [ ] `POST /api/tracker/approve-all` — all queued → pending
- [ ] `POST /api/tracker/reset-first-run/{source}` — reset first-run flag for radarr or sonarr

### base.html — nav update
- [ ] Add top-level tab navigation: Librarian | Tracker (active tab highlighted)
- [ ] Keep dark nav bar style, same colour scheme
