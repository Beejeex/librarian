# Phase 2 — Data Layer (highest risk — do before anything else imports models)

## Tasks
- [ ] `app/models.py` — add `TrackedItem` table (port from ref verbatim)
- [ ] `app/models.py` — extend `AppConfig` with all MadTracked fields:
      radarr_tags, sonarr_tags, poll_interval_minutes, share_path, copy_mode,
      radarr_first_run_complete, sonarr_first_run_complete, require_approval,
      max_concurrent_copies, max_share_size_gb, max_share_files,
      ntfy_url, ntfy_topic, ntfy_token, ntfy_on_copied, ntfy_on_error,
      ntfy_on_finished, ntfy_on_first_run
- [ ] `app/database.py` — add migration entries for every new AppConfig column
- [ ] `app/database.py` — add migration entry for TrackedItem `is_upgraded` column
