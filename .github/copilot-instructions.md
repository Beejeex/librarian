# Copilot Instructions — Librarian

## Project Overview

**Librarian** is a self-hosted, single-Docker-container tool with two integrated functions:

1. **Renamer** — fixes and standardises media library folder names in Radarr (movies) and Sonarr (TV shows). Detects folders whose names do not match the configured naming template, renames them on the mounted share, and updates the Radarr/Sonarr database paths via their REST APIs.

2. **Tracker** — monitors Radarr and Sonarr for tagged media items, copies their files to a configured share (`/share`), tracks state in SQLite, and marks items as "finished" when the copied file is deleted from the share.

Both tools run in the same container, share the same SQLite database, and are accessible through the same web UI.

### Renamer operator flow
**Scan → Review → Approve → Apply** — no changes applied without explicit approval.

- **Movies** (Radarr): each movie folder is renamed to `{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}`.
- **TV Shows** (Sonarr): each series folder is renamed to `{Series TitleYear} {tvdb-{TvdbId}}`.

### Tracker operator flow
**Poll (automatic) → Review queued → Approve → Copy** — files are copied to `/share` on each poll cycle; deleted files are marked finished automatically.

- **Movies** (Radarr): each tagged movie is tracked as a single item with its movie file.
- **TV Shows** (Sonarr): each tagged series is tracked at the episode file level — every episode file is individually copied and tracked, preserving `Series/Season XX/` structure on `/share`.

---

## Goals & Constraints

- **Docker-only**: everything runs in a single Docker container. No docker-compose split. Web server, rename engine, background scheduler, and file watcher all run inside one image.
- **No dev/prod split**: there is only one environment — the Docker container.
- **Mount point directories are pre-created in the Dockerfile** so the container starts cleanly even without volumes attached.
- **Mounts**:
  - `/media/movies` — read-write mount for Radarr library (folder renames happen here; also copy source for tracker)
  - `/media/tv` — read-write mount for Sonarr library (same)
  - `/share` — separate read-write mount for tracker copy destination (completely separate from `/media`)
- **No external database**: SQLite only, stored at `/config/librarian.db`.
- **No JS build pipeline**: UI is server-rendered with Jinja2 + HTMX + Alpine.js. JS assets are self-hosted under `app/static/js/`.
- **Renamer is on-demand only**: no background jobs for renaming.
- **Tracker is automatic**: APScheduler runs a poll loop at a configurable interval (default 15 min). Manual poll also available.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Strong ecosystem, async support, type hints |
| Web framework | FastAPI + Uvicorn | Async, typed, auto-docs |
| Templates | Jinja2 + HTMX + Alpine.js | Server-rendered, no JS build step; JS assets self-hosted |
| Server-Sent Events | sse-starlette | Streaming live output during Apply and copy progress |
| HTTP client | httpx (async) | Async HTTP for Radarr/Sonarr API calls |
| Scheduler | APScheduler (AsyncIOScheduler) | In-process poll loop for tracker |
| File watcher | watchdog | Monitors `/share` for deletions → marks items finished |
| Database | SQLite via SQLModel | Type-safe ORM built on SQLAlchemy + Pydantic |
| Container | python:3.12-slim | Everything in one image |

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Docker Container                     │
│                                                          │
│  ┌───────────────────────────────────────────────────┐   │
│  │   FastAPI Web UI (HTMX + Alpine.js)               │   │
│  │   - Renamer: Scan → Review → Apply (on-demand)    │   │
│  │   - Tracker: Dashboard, Items table, Poll Now     │   │
│  │   - Shared: Settings (tabbed), Logs               │   │
│  └──────────────────────┬────────────────────────────┘   │
│                         │                                │
│      ┌──────────────────┼──────────────────┐             │
│      │                  │                  │             │
│  ┌───▼────────┐  ┌───────▼───────┐  ┌──────▼───────┐    │
│  │APScheduler │  │  SQLite DB    │  │  watchdog    │    │
│  │Poll loop   │  │ /config/      │  │ /share mon   │    │
│  │(tracker)   │  │  librarian.db │  │ (tracker)    │    │
│  └────────────┘  └───────────────┘  └──────────────┘    │
│                                                          │
│  Volumes:                                                │
│    /config       → SQLite DB + app config                │
│    /media/movies → Radarr library (read-write)           │
│    /media/tv     → Sonarr library (read-write)           │
│    /share        → Tracker copy destination (read-write) │
└──────────────────────────────────────────────────────────┘
```

---

## Renamer Workflow

### Phase 1 — Scan
1. User selects **Radarr** or **Sonarr** and clicks **Scan**.
2. App fetches all movies/series from the arr API.
3. For each item, compute the expected folder name using the naming template.
4. Items where current ≠ expected are written to the DB as `RenameItem` with `status=pending`.
5. Each item's `disk_scenario` is classified by checking if old/new paths exist on disk.

### Phase 2 — Review
- Shows a table: **Current Name → Expected Name** with a coloured **Scenario** badge per row.
- User can approve / skip individual items or **Approve All**.

### Phase 3 — Apply
Scenario determines behaviour per item:
- `rename` — rename on disk + update arr path
- `arr_only` — skip disk, update arr path only
- `collision` — error immediately (both old and new folder exist)
- `missing` — error immediately (neither folder exists)

### Disk Scenario Values
| Scenario | Old exists | New exists | Action |
|---|---|---|---|
| `rename` | ✓ | ✗ | Rename disk + update arr |
| `arr_only` | ✗ | ✓ | Update arr only |
| `collision` | ✓ | ✓ | Error — skip |
| `missing` | ✗ | ✗ | Error — skip |
| `unknown` | — | — | Re-classify live at apply time |

### RenameItem Status Values
```
pending → approved → done
        ↘ skipped
pending → approved → error
```

---

## Tracker Workflow

### Status values
```
queued → pending → copied → finished
               ↘ error  (retried on next poll)
queued → finished  (skip — permanent)
```

### First-run index mode
Each source (Radarr, Sonarr) has its own `first_run_complete` flag. On first poll for a source (when it has tags configured):
1. Scan all tagged items → create `TrackedItem` rows with `status="queued"`, `is_backlog=True`.
2. Do **not** copy anything — user reviews and approves via UI.
3. Set the source's flag to `True` immediately after.

### Normal poll (after first run)
1. Fetch tagged items from arr.
2. New items land as `queued` (if `require_approval=True`) or `pending` (auto).
3. For each `pending` item: check quota → acquire semaphore → copy file → set `copied`.
4. **Upgrade detection**: if a `finished` item reappears with a changed file path, reset to `pending`, set `is_upgraded=True`.

### Quota system
- Backlog items (`is_backlog=True`) capped at `max_share_size_gb × 0.6` and `max_share_files × 0.6`.
- New items use full remaining capacity. Quota hit leaves the item as `pending` — retries next poll.
- `0` means unlimited for both caps.

### Share watcher
`watchdog` monitors `/share` for file deletions. When a `copied` file is deleted, its status is updated to `finished`.

---

## Folder Naming Rules (Renamer)

### Movies (Radarr)
Template: configurable, default `{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}`

### TV Series (Sonarr)
Template: configurable, default `{Series TitleYear} {tvdb-{TvdbId}}`

### CleanTitle Algorithm (`naming.py`)
1. Replace `: ` with ` - `
2. Replace standalone `:` with `-`
3. Remove characters: `? * " < > | \ /`
4. Collapse multiple spaces into one
5. Strip leading/trailing spaces and dots

---

## Database Schema (SQLModel)

### `AppConfig`
Single-row configuration table (id always = 1).

| Column | Type | Default | Notes |
|---|---|---|---|
| id | int PK | 1 | always 1 |
| radarr_url | str | `""` | |
| radarr_api_key | str | `""` | |
| radarr_root_folder | str | `/movies` | Path prefix Radarr uses in folder/file paths |
| radarr_folder_format | str | `{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}` | Renamer template |
| radarr_tags | str | `""` | Comma-separated tag names for tracker |
| sonarr_url | str | `""` | |
| sonarr_api_key | str | `""` | |
| sonarr_root_folder | str | `/tv` | Path prefix Sonarr uses |
| sonarr_folder_format | str | `{Series TitleYear} {tvdb-{TvdbId}}` | Renamer template |
| sonarr_tags | str | `""` | Comma-separated tag names for tracker |
| batch_size | int | `20` | Renamer: items per apply batch |
| poll_interval_minutes | int | `15` | Tracker: scheduler interval |
| share_path | str | `/share` | Tracker: copy destination root |
| copy_mode | str | `copy` | Always `copy` |
| radarr_first_run_complete | bool | `False` | Tracker: set after first index-only Radarr poll |
| sonarr_first_run_complete | bool | `False` | Tracker: set after first index-only Sonarr poll |
| require_approval | bool | `False` | Tracker: post-first-run items land as queued when True |
| max_concurrent_copies | int | `2` | Tracker: asyncio.Semaphore width per poll |
| max_share_size_gb | float | `0.0` | Tracker: total size cap in GB; 0 = unlimited |
| max_share_files | int | `0` | Tracker: total file count cap; 0 = unlimited |
| ntfy_url | str | `https://ntfy.sh` | Notifications base URL |
| ntfy_topic | str | `""` | Notifications topic; empty = disabled |
| ntfy_token | str | `""` | Optional Bearer token |
| ntfy_on_copied | bool | `True` | Notify on successful copy |
| ntfy_on_error | bool | `True` | Notify on copy error |
| ntfy_on_finished | bool | `True` | Notify on file deleted from share |
| ntfy_on_first_run | bool | `True` | Notify when first-run index completes |
| created_at | datetime | now | |
| updated_at | datetime | now | |

### `ScanRun`
One row per Scan invocation (Renamer).

| Column | Type | Notes |
|---|---|---|
| id | int PK | auto |
| source | str | `radarr` or `sonarr` |
| status | str | `scanning`, `ready`, `applying`, `done`, `error` |
| total_items | int | Total mismatches found |
| done_count | int | Successfully renamed |
| error_count | int | Errors |
| created_at | datetime | |
| updated_at | datetime | |

### `RenameItem`
One row per folder mismatch (Renamer).

| Column | Type | Notes |
|---|---|---|
| id | int PK | auto |
| scan_run_id | int FK | FK → ScanRun.id |
| source | str | `radarr` or `sonarr` |
| source_id | int | Movie/Series ID in arr |
| title | str | Display title |
| current_folder | str | Current folder basename |
| expected_folder | str | Target folder basename |
| current_path | str | Full arr-view path |
| expected_path | str | Full expected arr-view path |
| status | str | `pending`, `approved`, `skipped`, `done`, `error` |
| disk_scenario | str | `rename`, `arr_only`, `collision`, `missing`, `unknown` |
| error_message | str\|None | Error detail |
| created_at | datetime | |
| updated_at | datetime | |

### `TrackedItem`
One row per media file tracked by Tracker.

| Column | Type | Notes |
|---|---|---|
| id | int PK | auto |
| source | str | `radarr` or `sonarr` |
| media_type | str | `movie` or `episode` |
| source_id | int | Movie ID (Radarr) or episode file ID (Sonarr) |
| series_id | int\|None | Sonarr series ID; null for movies |
| title | str | Movie title or `Series S01E02` |
| series_title | str\|None | Series name (Sonarr only) |
| season_number | int\|None | Season number (Sonarr only) |
| episode_number | int\|None | Episode number (Sonarr only) |
| file_path | str | Original file path on source media mount |
| share_path | str | Destination path on /share |
| status | str | `queued`, `pending`, `copied`, `finished`, `error` |
| is_backlog | bool | True = discovered during first-run index |
| is_upgraded | bool | True = source file path changed after item was finished |
| file_size_bytes | int | Recorded at copy time; used for quota accounting |
| error_message | str\|None | Populated when status is `error` |
| tag | str | Comma-separated matched tag names |
| created_at | datetime | |
| updated_at | datetime | |

---

## Project Structure

```
librarian/
├── Dockerfile
├── requirements.txt
├── pytest.ini
├── ref/                   # MadTracked source (reference only — do not import from here)
├── docs/
├── todo/
├── app/
│   ├── main.py            # FastAPI app, startup lifespan (DB + scheduler + watcher)
│   ├── config.py          # Settings loader (env vars + DB)
│   ├── database.py        # SQLite engine, session factory, migrations
│   ├── models.py          # AppConfig, ScanRun, RenameItem, TrackedItem
│   ├── version.py         # Single VERSION constant
│   │
│   ├── # --- Renamer ---
│   ├── naming.py
│   ├── arr_client.py
│   ├── radarr.py          # fetch movies/tags, fetch_folder_format, update_movie_path
│   ├── sonarr.py          # fetch series/tags/episode files, fetch_folder_format, update_series_path
│   ├── scanner.py
│   ├── renamer.py
│   │
│   ├── # --- Tracker ---
│   ├── scheduler.py       # APScheduler poll loop, first-run, quota, copy dispatch
│   ├── watcher.py         # watchdog: /share delete → copied→finished
│   ├── copier.py          # chunked copy, quota helpers, subtitle detection
│   ├── copy_progress.py   # in-memory per-item copy progress for SSE
│   ├── notifier.py        # ntfy.sh push notifications
│   │
│   ├── log_buffer.py
│   ├── routers/
│   │   ├── ui.py          # Renamer pages
│   │   ├── api.py         # Renamer API + SSE
│   │   ├── tracker_ui.py  # Tracker pages (/tracker, /tracker/items, /tracker/logs)
│   │   └── tracker_api.py # Tracker API (/api/tracker/*)
│   ├── static/js/
│   │   ├── htmx.min.js
│   │   └── alpine.min.js
│   └── templates/
│       ├── base.html
│       ├── dashboard.html
│       ├── review.html
│       ├── settings.html          # Tabbed: Librarian | Tracker | Notifications
│       ├── logs.html
│       ├── tracker_dashboard.html
│       ├── tracker_items.html
│       └── tracker_logs.html
└── tests/
    ├── conftest.py
    ├── test_naming.py
    ├── test_radarr.py
    ├── test_sonarr.py
    ├── test_scanner.py
    ├── test_renamer.py
    ├── test_api.py
    ├── test_copier.py
    ├── test_scheduler.py
    └── test_watcher.py
```

---

## Docker Details

- Base image: `python:3.12-slim`
- Exposed port: `8080`
- Pre-created mount points:
  - `/config` — SQLite DB
  - `/media/movies` — Radarr library (read-write)
  - `/media/tv` — Sonarr library (read-write)
  - `/share` — Tracker copy destination (read-write; separate volume from /media)

```dockerfile
RUN mkdir -p /config /media/movies /media/tv /share
```

- Environment variables (all optional):

  **Renamer:** `RADARR_URL`, `RADARR_API_KEY`, `RADARR_ROOT_FOLDER` (default `/movies`), `SONARR_URL`, `SONARR_API_KEY`, `SONARR_ROOT_FOLDER` (default `/tv`), `BATCH_SIZE` (default `20`)

  **Tracker:** `RADARR_TAGS`, `SONARR_TAGS`, `POLL_INTERVAL_MINUTES` (default `15`), `REQUIRE_APPROVAL` (default `false`), `MAX_CONCURRENT_COPIES` (default `2`), `MAX_SHARE_SIZE_GB` (default `0`), `MAX_SHARE_FILES` (default `0`), `SHARE_PATH` (default `/share`)

  **Notifications:** `NTFY_URL` (default `https://ntfy.sh`), `NTFY_TOPIC` (default `""`), `NTFY_TOKEN` (default `""`)

  **General:** `TZ` (default `UTC`)

### Run the app
```powershell
docker run -d `
  -p 8080:8080 `
  -v /path/to/config:/config `
  -v /path/to/movies:/media/movies `
  -v /path/to/tv:/media/tv `
  -v /path/to/share:/share `
  --name librarian `
  librarian
```

---

## UI Pages

Dark nav bar (`#1e293b`), light page body (`#f1f5f9`), white cards. Top nav has two tabs: **Librarian** and **Tracker**.

| Route | Description |
|---|---|
| `/` | Unified dashboard: Tracker stat card + Renamer last-scan card |
| `/review` | Renamer mismatch table with scenario badges |
| `/apply` | Renamer SSE live output |
| `/settings` | Tabbed settings: Librarian \| Tracker \| Notifications |
| `/logs` | Renamer log output |
| `/tracker` | Tracker dashboard: stat cards, first-run banner, Poll Now |
| `/tracker/items` | All TrackedItems: approve/skip/reset, status filter |
| `/tracker/logs` | Tracker SSE live copy log |

---

## Radarr / Sonarr API Reference

### Radarr
| Operation | Endpoint |
|---|---|
| Fetch all movies | `GET /api/v3/movie` |
| Fetch all tags | `GET /api/v3/tag` |
| Update movie path | `PUT /api/v3/movie/{id}?moveFiles=false` |
| Fetch naming config | `GET /api/v3/config/naming` |

### Sonarr
| Operation | Endpoint |
|---|---|
| Fetch all series | `GET /api/v3/series` |
| Fetch episode files | `GET /api/v3/episodefile?seriesId={id}` |
| Fetch all tags | `GET /api/v3/tag` |
| Update series path | `PUT /api/v3/series/{id}?moveFiles=false` |
| Fetch naming config | `GET /api/v3/config/naming` |

- Authentication: `X-Api-Key: <key>` header.
- Always GET before PUT — never construct a partial body.
- `moveFiles=false` on all PUTs — arr must never trigger physical moves.

### Path Remapping
```
arr path:        /movies/Dune.2021.2160p/
container path:  /media/movies/Dune.2021.2160p/
```
`remap_to_container(arr_path, root_folder, media_path)` in `renamer.py`.

---

## Tracker Copy Destination Structure
```
/share/
  Movie Title (Year)/
    Movie Title (Year).mkv
    Movie Title (Year).en.srt    ← subtitle copied alongside
  Series Title/
    Season 01/
      Series.Title.S01E01.mkv
```

---

## Instructions File Maintenance

- **Always update `.github/copilot-instructions.md`** as part of any change that affects architecture, schema, module behaviour, UI, or workflow.
- Copilot reads this file at the start of every session — stale instructions cause incorrect suggestions.

---

## Development Environment

- **Host OS**: Windows — all terminal commands must use **PowerShell** syntax.
- Use `;` to chain commands (not `&&`).
- The app runs exclusively inside Docker.

---

## Build, Test & Commit

1. `docker build -t librarian .`
2. `docker run --rm librarian pytest -v`
3. `git add -A; git commit -m "<description>"`

Never leave uncommitted changes after a passing build+test cycle.

---

## Coding Standards

- **Type hints** everywhere.
- **async/await** for all I/O.
- Keep modules focused — no business logic in route handlers.
- Log with `logging`: INFO normal, WARNING skips, ERROR failures.
- Config readable from env vars on startup; DB row is authoritative at runtime.
- API keys and tokens must **never** appear in log output.

### Code Reuse & DRY
- `naming.py` — single source of truth for folder name computation.
- `renamer.py` — single source of truth for disk rename operations.
- `copier.py` — single source of truth for file copy operations.
- `arr_client.py` — shared base client for Radarr and Sonarr.

---

## Error Handling

- API call fails → log error, surface in UI, do not crash.
- Disk rename fails → mark item `error`, log, continue.
- Arr update fails after disk rename → mark `error`, log clearly that disk was changed but arr was not.
- Copy fails → mark TrackedItem `error`; retried on next poll.
- Single item error never aborts the batch/poll.

---

## Graceful Shutdown

On SIGTERM, lifespan context must:
1. Stop APScheduler
2. Stop watchdog observer
3. Dispose SQLAlchemy connection pool

---

## Health Check

`GET /health` → `{"status": "ok"}` HTTP 200.
Dockerfile: `HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1`

---

## Testing Guidelines

- `pytest` + `pytest-asyncio`.
- Mock arr APIs with `respx` — never call real endpoints.
- In-memory SQLite for all DB tests.
- `tmp_path` for disk ops and copy tests.

### Fixtures (conftest.py)
- `db_session` — in-memory SQLite, fresh per test
- `mock_radarr` / `mock_sonarr` — respx routers with sample responses
- `tmp_media` — tmp_path library directory with pre-created sample folder
- `tmp_share` — tmp_path share directory for copy destination tests
