# Phase 4 — Extend Existing Modules

## Tasks

### config.py
- [ ] `load_config()` — handle all new AppConfig fields (tags, poll, ntfy, quota, etc.)
- [ ] `save_config()` — persist new fields from settings form
- [ ] `mask_secrets()` — also mask ntfy_token

### radarr.py
- [ ] Add `fetch_tag_ids(tag_names: list[str]) -> list[int]` — `GET /api/v3/tag`
- [ ] Add `fetch_tagged_movies(tag_ids: list[int]) -> list[dict]` — filter movies by tag

### sonarr.py
- [ ] Add `fetch_tag_ids(tag_names: list[str]) -> list[int]` — `GET /api/v3/tag`
- [ ] Add `fetch_tagged_series(tag_ids: list[int]) -> list[dict]` — filter series by tag
- [ ] Add `fetch_episode_files(series_id: int) -> list[dict]` — `GET /api/v3/episodefile?seriesId=<id>`

### main.py
- [ ] Import scheduler + watcher lifecycle functions
- [ ] Extend lifespan startup: reset stuck `copying` items, start scheduler, start watcher
- [ ] Extend lifespan shutdown: stop scheduler, stop watcher, then dispose engine
- [ ] Include two new routers: tracker UI router and tracker API router
