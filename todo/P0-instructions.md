# Phase 0 — Copilot Instructions Update

Update `.github/copilot-instructions.md` to document the combined Librarian + MadTracked app before any code changes are made.

## Tasks
- [ ] Rewrite overview section to describe both tools in one container
- [ ] Add MadTracked architecture block (scheduler, watcher, copier, notifier)
- [ ] Add TrackedItem and extended AppConfig to schema section
- [ ] Add new modules to project structure section
- [ ] Add new env vars (RADARR_TAGS, SONARR_TAGS, NTFY_*, POLL_INTERVAL_MINUTES, etc.)
- [ ] Add new UI routes (/tracker, /tracker/items)
- [ ] Update Docker volume list to include /share (separate from /media)
