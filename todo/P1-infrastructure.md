# Phase 1 — Infrastructure

## Tasks
- [ ] `requirements.txt` — add `apscheduler==3.11.0` and `watchdog==6.0.0`
- [ ] `Dockerfile` — add `/share` to `RUN mkdir -p ...`
- [ ] `Dockerfile` — add `SHARE_PATH` env var (default `/share`)
- [ ] `Dockerfile` — add tracker env vars: `RADARR_TAGS`, `SONARR_TAGS`, `POLL_INTERVAL_MINUTES`, `REQUIRE_APPROVAL`, `MAX_CONCURRENT_COPIES`, `MAX_SHARE_SIZE_GB`, `MAX_SHARE_FILES`, `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN`
