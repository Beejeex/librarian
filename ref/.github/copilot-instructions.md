# Copilot Instructions — MadTracked

## Project Overview

**MadTracked** is a self-hosted, single-Docker-container tool that monitors Radarr (movies) and Sonarr (TV shows) for tagged media items, copies their files to a configured share (mounted volume), tracks state in a SQLite database, and marks items as "finished" when the copied file is deleted from the share.

- **Movies** (Radarr): each tagged movie is tracked as a single item with its movie file.
- **TV Shows** (Sonarr): each tagged series is tracked at the **episode file** level — every episode file for the series is individually copied and tracked. TV shows preserve the `Series/Season XX/` folder structure on the share.

---

## Goals & Constraints

- **Docker-only**: the entire application runs in a single Docker container. No docker-compose services split (no separate DB container, no separate worker container). Everything — web server, background scheduler, file watcher — runs inside one image.
- **Everything is inside the container**: all application code, dependencies, and runtime live in the image. Nothing runs on the host directly.
- **No dev/prod split**: there is only one environment — the Docker container. No separate dev server, no environment-specific config files, no hot-reload tooling. What runs locally is identical to what runs in production.
- **Mount point directories are pre-created in the Dockerfile**: `/config`, `/media/movies`, `/media/tv`, and `/share` are created with `mkdir -p` in the Dockerfile so the container starts cleanly even without volumes attached.
- **Shares are Docker volume mounts**: the host binds external paths into `/media/movies` and `/media/tv` (read-only) and `/share` (read-write). File operations inside the container use those paths via Python `shutil` — no network shares or SMB/NFS needed.
- **Path remapping**: Radarr/Sonarr report file paths using their own host-side root folders (e.g. `/movies/Film/file.mkv`). The scheduler strips this prefix (configured as `radarr_root_folder` / `sonarr_root_folder`) and prepends `/media/movies` or `/media/tv` to get the container-local path before copying.
- **No external database**: SQLite only, stored at `/config/madtracked.db` inside the container, persisted via the `/config` volume mount.
- **No JS build pipeline**: UI is server-rendered with Jinja2 templates + HTMX + Alpine.js. No React, Vue, or npm. JS assets (htmx, Alpine.js) are bundled into `app/static/js/` and served by FastAPI — no CDN dependencies.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Strong ecosystem, async support, type hints |
| Web framework | FastAPI + Uvicorn | Async, typed, auto-docs, easy background tasks |
| Templates | Jinja2 + HTMX + Alpine.js | Server-rendered, no JS build step; JS assets self-hosted under `app/static/js/` |
| Server-Sent Events | sse-starlette | Streaming log/event push to the UI |
| HTTP client | httpx (async) | Async HTTP for Radarr/Sonarr API calls |
| Scheduler | APScheduler (AsyncIOScheduler) | In-process scheduling, no Celery/Redis needed |
| File watcher | watchdog | Cross-platform inotify-style watcher for the share |
| Database | SQLite via SQLModel | Type-safe ORM built on SQLAlchemy + Pydantic |
| Container | python:3.12-slim | Everything in one image |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Docker Container                   │
│                                                     │
│  ┌──────────┐   ┌──────────────┐  ┌─────────────┐  │
│  │ FastAPI  │   │  APScheduler │  │  watchdog   │  │
│  │ Web UI   │   │  Poll loop   │  │  Share mon  │  │
│  └────┬─────┘   └──────┬───────┘  └──────┬──────┘  │
│       │                │                 │          │
│       └────────────────┼─────────────────┘          │
│                        │                            │
│              ┌─────────▼──────────┐                 │
│              │   SQLite (SQLModel) │                 │
│              └────────────────────┘                 │
│                                                     │
│  Volumes:                                           │
│    /config       → SQLite DB + app config           │
│    /media/movies → Radarr source files (read-only)  │
│    /media/tv     → Sonarr source files (read-only)  │
│    /share        → Destination share (read-write)   │
└─────────────────────────────────────────────────────┘
```

---

## Core Workflow

### Status values
```
queued → pending → copied → finished
                ↘ error  (retried on next poll)
queued → finished  (skip — permanent, no copy ever)
```
- `queued` — discovered but awaiting approval
- `pending` — approved, waiting for the copy semaphore / quota headroom
- `copied` — file successfully on /share
- `finished` — file deleted from /share (done) or manually skipped
- `error` — last copy attempt failed; retried automatically on next poll

### Upgrade detection
On every poll, if a `finished` item still appears in Radarr/Sonarr (same `source_id`) but its reported file path has changed, MadTracked treats this as an upgraded file:
1. Reset `status` → `pending`, set `is_upgraded = True`, update `file_path`, `share_path`, and `file_size_bytes`.
2. The new version will be copied on the next copy pass.
3. `copied` items are **never** reset by an upgrade — the file is still on the share and the user has not yet deleted it.

### First-run index mode

Each source (Radarr, Sonarr) has its own first-run flag (`radarr_first_run_complete` / `sonarr_first_run_complete`). A source's first-run only triggers when:
- Its flag is `False`, **and**
- It has at least one tag configured (non-empty `radarr_tags` / `sonarr_tags`)

On first-run for a source:
1. Scan all tagged movies (Radarr) or episode files (Sonarr).
2. Create a `TrackedItem` for each with `status="queued"` and `is_backlog=True`.
3. Do **not** copy anything.
4. Set the source's flag to `True` in the DB immediately after.

The two sources are independent — configuring only Radarr never blocks or affects Sonarr's first-run, and vice versa.

The dashboard shows a per-source first-run banner only when a source has tags and its flag is not yet set. After the first-run index poll completes, the banner switches to an "Approve All & Start" prompt if there are queued items.

### Normal poll (after first run)

1. **Poll Radarr/Sonarr** (configurable interval, default 15 min):
   - **Radarr (movies)**: fetch all movies via `GET /api/v3/movie`, filter by configured tag, copy the movie file to `/share/<MovieTitle (Year)>/` and record as a single `TrackedItem`.
   - **Sonarr (TV shows)**: fetch all series via `GET /api/v3/series`, filter by configured tag, then fetch all episode files via `GET /api/v3/episodefile?seriesId=<id>`. Copy each episode file to `/share/<Series Title>/Season XX/` preserving the original filename. Each episode file is its own `TrackedItem` row.
   - New items get `is_backlog=False`. If `require_approval=True`, they land as `queued`; otherwise they go straight to `pending` and are copied this poll.
   - For each `pending` item: check quota → acquire semaphore → copy → record `file_size_bytes` → set `copied`.

2. **Quota system** — enforced per poll before each copy, using DB-based accounting (sum of `file_size_bytes` on `status="copied"` rows):
   - Backlog items (`is_backlog=True`) are capped at `max_share_size_gb × 0.6` and `max_share_files × 0.6`.
   - New items (`is_backlog=False`) can use the **full remaining capacity** — their check is against the total cap minus all current usage (backlog + new combined). Unused backlog headroom is always available to new items.
   - A quota hit leaves the item as `pending` (not `error`) — it retries automatically on the next poll as the watcher frees space.
   - `max_share_size_gb = 0` and `max_share_files = 0` both mean unlimited.

3. **Concurrency** — `asyncio.Semaphore(max_concurrent_copies)` limits parallel file copies within a single poll run.

4. **Watch the share** (`/share` directory) with `watchdog`:
   - When a file that exists in the DB with status `copied` is **deleted** from `/share`, update its DB status to `finished`.

5. **Never re-copy** a file with status `finished`. Once finished, it is considered done unless manually reset via the UI.

---

## Database Schema (SQLModel)

### `TrackedItem`
| Column | Type | Notes |
|---|---|---|
| id | int PK | auto |
| source | str | `radarr` or `sonarr` |
| media_type | str | `movie` or `episode` |
| source_id | int | Movie ID (Radarr) or episode file ID (Sonarr) |
| series_id | int \| None | Sonarr series ID (null for movies) |
| title | str | Movie title or "Series S01E02" |
| series_title | str \| None | Series name (Sonarr only) |
| season_number | int \| None | Season number (Sonarr only) |
| episode_number | int \| None | Episode number (Sonarr only) |
| file_path | str | Original file path on source media |
| share_path | str | Destination path on /share |
| status | str | `queued`, `pending`, `copied`, `finished`, `error` |
| is_backlog | bool | True = discovered on first-run index; False = discovered after |
| is_upgraded | bool | True = source file path changed after item was finished; reset to pending for re-copy |
| file_size_bytes | int | Actual file size recorded at copy time; used for quota accounting |
| tag | str | Comma-separated names of all tags that matched this item, e.g. `"share,watched"` |
| created_at | datetime | |
| updated_at | datetime | |

### `AppConfig`
| Column | Type | Notes |
|---|---|---|
| id | int PK | always 1 (single-row config) |
| radarr_url | str | e.g. `http://192.168.1.10:7878` |
| radarr_api_key | str | |
| radarr_tags | str | Comma-separated tag names to watch in Radarr, e.g. `"share,watched"` |
| radarr_root_folder | str | Path prefix Radarr uses in file paths, default `/movies` |
| sonarr_url | str | |
| sonarr_api_key | str | |
| sonarr_tags | str | Comma-separated tag names to watch in Sonarr |
| sonarr_root_folder | str | Path prefix Sonarr uses in file paths, default `/tv` |
| poll_interval_minutes | int | default 15 |
| share_path | str | default `/share` |
| copy_mode | str | always `copy`; hardlink removed from UI |
| radarr_first_run_complete | bool | Set after first index-only Radarr poll; only active when radarr_tags is set; never via env |
| sonarr_first_run_complete | bool | Set after first index-only Sonarr poll; only active when sonarr_tags is set; never via env |
| require_approval | bool | When True, new items (post first-run) also land as `queued` |
| max_concurrent_copies | int | Semaphore width for parallel copies per poll; default 2 |
| max_share_size_gb | float | Total share size cap in GB; 0 = unlimited. Backlog: 60%, New: 40% |
| max_share_files | int | Total file count cap; 0 = unlimited. Same 60/40 split as size |
| radarr_root_folder | str | Path prefix Radarr uses in file paths, default `/movies` |
| sonarr_root_folder | str | Path prefix Sonarr uses in file paths, default `/tv` |
| ntfy_url | str | ntfy server base URL; default `https://ntfy.sh`; override for self-hosted |
| ntfy_topic | str | ntfy topic to publish to; empty = notifications disabled |
| ntfy_token | str | Optional Bearer token for protected ntfy topics |

---

## Project Structure

```
madtracked/
├── Dockerfile
├── requirements.txt
├── pytest.ini
├── todo/                  # Implementation task files (one per module)
├── app/
│   ├── main.py            # FastAPI app, startup lifespan
│   ├── config.py          # Settings loader (env vars + DB)
│   ├── database.py        # SQLite engine, session factory
│   ├── models.py          # SQLModel table definitions
│   ├── scheduler.py       # APScheduler setup + poll task + _remap_media_path()
│   ├── watcher.py         # watchdog share monitor
│   ├── arr_client.py      # Shared base HTTP client (ArrClient) for Radarr + Sonarr
│   ├── radarr.py          # Radarr API client
│   ├── sonarr.py          # Sonarr API client
│   ├── copier.py          # File copy / hardlink logic
│   ├── notifier.py        # ntfy.sh push notifications (copied, error, finished, first-run)
│   ├── log_buffer.py      # In-memory deque of recent log lines for UI
│   ├── routers/
│   │   ├── ui.py          # HTMX + Alpine.js + Jinja2 page routes
│   │   └── api.py         # REST API routes
│   ├── static/
│   │   └── js/
│   │       ├── htmx.min.js    # htmx 1.9.12 (self-hosted, no CDN)
│   │       └── alpine.min.js  # Alpine.js 3.14.1 (self-hosted, no CDN)
│   └── templates/
│       ├── base.html
│       ├── dashboard.html
│       ├── config.html
│       ├── items.html
│       └── logs.html
└── tests/
```

---

## Docker Details

- Base image: `python:3.12-slim`
- Exposed port: `8080`
- Mount point directories **pre-created in the Dockerfile** (`mkdir -p`) so the container starts cleanly even without volumes attached:
  - `/config` — SQLite DB (`madtracked.db`) persisted via volume mount
  - `/media/movies` — read-only mount for Radarr source files
  - `/media/tv` — read-only mount for Sonarr source files
  - `/share` — read-write destination share (bind-mount from host)
- Dockerfile example:
  ```dockerfile
  RUN mkdir -p /config /media/movies /media/tv /share
  ```
- Environment variables (all optional, can also be set via UI):
  - `RADARR_URL`, `RADARR_API_KEY`, `RADARR_TAGS` (comma-separated, e.g. `share,watched`), `RADARR_ROOT_FOLDER` (default: `/movies`)
  - `SONARR_URL`, `SONARR_API_KEY`, `SONARR_TAGS` (comma-separated), `SONARR_ROOT_FOLDER` (default: `/tv`)
  - `NTFY_URL` (default: `https://ntfy.sh`), `NTFY_TOPIC` (default: `""` = disabled), `NTFY_TOKEN` (default: `""`)
  - `POLL_INTERVAL_MINUTES` (default: `15`)
  - `REQUIRE_APPROVAL` (default: `false`)
  - `MAX_CONCURRENT_COPIES` (default: `2`)
  - `MAX_SHARE_SIZE_GB` (default: `0` = unlimited)
  - `MAX_SHARE_FILES` (default: `0` = unlimited)
  - `TZ` (default: `UTC`)
  - `first_run_complete` is **never** set via env — always managed programmatically

---

## UI Pages

The UI uses a **mixed light/dark theme**: dark nav bar (`#1e293b`), light page body (`#f1f5f9`), white cards with subtle shadows. No external CSS framework — all styling is inline in `base.html`.

| Route | Description |
|---|---|
| `/` | Dashboard: stat cards (Total/Queued/Copied/Finished/Error), first-run banner with Approve All & Start (Alpine.js), share usage progress bars (polled every 30s), Poll Now button, recent activity table |
| `/items` | Table of all tracked items with status badges; Approve/Skip (queued), Re-copy (copied), Reset/Retry (finished/error); Alpine.js status filter bar; backlog badge on queued rows |
| `/config` | Radarr/Sonarr config, copy mode radio (Alpine.js hardlink warning), require-approval checkbox, max concurrent copies, max share size GB, max share files, poll interval, share path; ntfy.sh notification section (topic, server URL, token); Danger Zone: Reset first-run index button |
| `/logs` | Tail of recent log output with a "Clear logs" button |

---

## Instructions File Maintenance

- **Always update `.github/copilot-instructions.md`** as part of any change that affects architecture, schema, module behaviour, UI, or workflow — before committing.
- The instructions file is the authoritative reference for how the project works. It must reflect the current implementation at all times.
- When adding a new field, module, route, or behavioural rule, update the relevant section (schema table, project structure, workflow description, etc.).
- Copilot reads this file at the start of every session — stale instructions cause incorrect code suggestions.

---

## Development Environment

- **Host OS**: Windows — all terminal commands must use **PowerShell** syntax.
- Use `;` to chain commands (not `&&`), PowerShell cmdlets (`Select-Object`, `Where-Object`, etc.) instead of Unix tools (`grep`, `tail`, `cat`).
- The app itself runs exclusively inside Docker — no local Python install is required or used.
- Docker Desktop for Windows is the container runtime.

---

## Build, Test & Commit

After completing any set of code changes:

1. **Build** — `docker build -t madtracked .`
2. **Test** — `docker run --rm madtracked pytest -v`
3. **Commit** — if all tests pass, always commit with a descriptive message:
   ```powershell
   git add -A; git commit -m "<short description of what changed>"
   ```

- Never leave uncommitted changes after a successful build+test cycle.
- Commit messages should be imperative, present-tense and specific (e.g. `Add per-source first-run flags`, `Copy subtitle files alongside video`).

---

## Coding Standards

- Use **type hints** everywhere.
- Use **async/await** for all I/O (HTTP calls via `httpx`, file ops can be sync in a thread pool via `asyncio.to_thread`).
- Keep each module focused — no business logic in route handlers.
- Log with Python's `logging` module at appropriate levels (INFO for normal flow, WARNING for skipped items, ERROR for failures).
- All configuration must be readable from environment variables on startup, with the DB config row as the authoritative source at runtime.
- Write tests for the Radarr/Sonarr API clients and the copy logic using `pytest` + `pytest-asyncio`.

### Code Comments & Clarity

- Every module must have a top-of-file docstring explaining **what the module does and why it exists**.
- Every function/method must have a short docstring: one sentence on **what it does**, plus notes on non-obvious parameters or return values.
- Use inline comments for non-obvious logic — explain the **why**, not the **what**. Keep them concise (one line).
- Avoid over-commenting simple code. A clear function name + type hints are often enough.
- Group related logic with a short section comment (e.g. `# --- Resolve tag IDs ---`) to make long functions scannable.

### Code Reuse & DRY

- **No duplicate logic.** If the same logic is needed in more than one place, extract it into a shared function.
- Shared helpers go in a dedicated module (e.g. `app/utils.py`) rather than being inlined or copied.
- Radarr and Sonarr clients share common HTTP/auth patterns — extract a `BaseArrClient` or shared `make_request` helper rather than duplicating per-client.
- File copy/hardlink logic lives exclusively in `copier.py` — never inline file operations in the scheduler or routes.
- DB query patterns that repeat (e.g. "find item by source + source_id") must be a named function in a repository/service layer, not repeated ad-hoc across modules.

Example style:
```python
async def get_tagged_movies(client: httpx.AsyncClient, tag_id: int) -> list[dict]:
    """Fetch all Radarr movies that have the given tag ID applied."""
    movies = await client.get("/api/v3/movie")
    # Only include movies that have a file — unmonitored/missing movies are skipped
    return [m for m in movies.json() if tag_id in m["tags"] and m.get("movieFile")]
```

---

## Radarr / Sonarr API Notes

### Radarr (Movies)
- `GET /api/v3/movie` — returns all movies; each has a `tags` list of tag IDs and a `movieFile` object with the file path.
- `GET /api/v3/tag` — resolve tag name → ID.
- A movie is eligible when its `tags` list contains **any** of the configured tag IDs and `movieFile` exists.
- Tag names are comma-separated in `AppConfig.radarr_tags`; resolve each independently.

### Sonarr (TV Shows)
- `GET /api/v3/series` — returns all series; each has a `tags` list. Filter by configured tag ID.
- `GET /api/v3/episodefile?seriesId=<id>` — returns all episode files for a series. Each has `path`, `seasonNumber`, and `seriesId`.
- `GET /api/v3/episode?episodeFileId=<id>` — used to resolve episode number/title for display.
- `GET /api/v3/tag` — resolve tag name → ID.
- Tag names are comma-separated in `AppConfig.sonarr_tags`; resolve each independently. A series is eligible if its `tags` list contains **any** of the configured tag IDs.

### Shared
- Authentication: `X-Api-Key` header on all requests.
- Tag IDs are resolved from names at poll time via `resolve_tag_id()` in `ArrClient`. Called once per tag name per poll; results are NOT cached between polls.
- A media item is eligible if its `tags` list contains **any** of the resolved tag IDs (union match).
- If a file matches multiple tags, all matching tag names are stored comma-separated in `TrackedItem.tag`. Only one `TrackedItem` row is ever created per `source_id` (no duplicates).
- Use `httpx.AsyncClient` for all API calls.

### Path Remapping
Radarr/Sonarr report file paths as they exist on their own host (e.g. `/movies/Film/file.mkv`).
The container mounts those same files at `/media/movies/` and `/media/tv/`.
`_remap_media_path(file_path, root_folder, subfolder)` in `scheduler.py` translates:
```
/movies/Film/file.mkv  →  /media/movies/Film/file.mkv   (radarr_root_folder=/movies)
/tv/Show/S01/ep.mkv    →  /media/tv/Show/S01/ep.mkv     (sonarr_root_folder=/tv)
```
`radarr_root_folder` and `sonarr_root_folder` are configurable per the UI and env vars.

### Tag API Endpoints (HTMX helpers)
- `GET /api/radarr/tags?radarr_url=...&radarr_api_key=...&selected=...` — proxies Radarr's `/api/v3/tag` and returns an HTML `<select multiple>` fragment. `selected` is a comma-separated string of currently chosen tag names; matching options are pre-selected.
- `GET /api/sonarr/tags?sonarr_url=...&sonarr_api_key=...&selected=...` — same for Sonarr.
These are used by the config page to populate tag dropdowns without a page reload.

---

## Building & Testing

Everything builds and tests inside Docker — no local Python install required.

### Build the image
```powershell
docker build -t madtracked .
```

### Run the app
```powershell
docker run -d `
  -p 8080:8080 `
  -v /path/to/config:/config `
  -v /path/to/movies:/media/movies:ro `
  -v /path/to/tv:/media/tv:ro `
  -v /path/to/share:/share `
  --name madtracked `
  madtracked
```

### Run tests (inside the container — no local Python needed)
```powershell
# Run the full test suite
docker run --rm madtracked pytest

# Run a specific test file
docker run --rm madtracked pytest tests/test_radarr.py

# Run with verbose output
docker run --rm madtracked pytest -v
```

### Rebuild and test in one command
```powershell
docker build -t madtracked .; docker run --rm madtracked pytest -v
```

> Tests use in-memory SQLite and mocked HTTP — no real Radarr/Sonarr or share mount is needed to run them.

---

## Error Handling

- If a Radarr/Sonarr API call fails (network error, non-2xx response), log the error and skip that poll cycle — do not crash the scheduler.
- If a file copy fails, set the `TrackedItem` status to `error` and store the error message. It will be retried on the next poll.
- Never let a single item failure abort the entire poll run — catch exceptions per-item and continue.
- API keys must **never** appear in log output. Mask them before logging config values.

---

## Idempotency & Concurrency

- The poll job must be **idempotent**: running it twice in a row must produce the same result with no duplicates.
- Use a simple boolean flag or asyncio lock to prevent overlapping poll runs. If a poll is already in progress when the next trigger fires, skip the new run and log a warning.
- SQLite writes are serialized through the session factory — never share a session across tasks or threads.

---

## Graceful Shutdown

- On container SIGTERM, FastAPI's lifespan context must cleanly:
  1. Stop the APScheduler (no new jobs fired).
  2. Stop the watchdog observer thread.
  3. Close the SQLite connection pool.
- This prevents partial writes and avoids zombie threads on container restart.

---

## Health Check

- Expose `GET /health` returning `{"status": "ok"}` with HTTP 200.
- Used by Docker's `HEALTHCHECK` directive to confirm the app is running.
- Add to Dockerfile: `HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1`

---

## Testing Guidelines

- Use `pytest` + `pytest-asyncio` for all tests.
- **Never** call real Radarr/Sonarr APIs in tests — mock `httpx.AsyncClient` responses with `respx` or `pytest-httpx`.
- Use a temporary in-memory SQLite DB for all DB tests (`sqlite:///:memory:`).
- Test the copy logic with a `tmp_path` fixture — no real share needed.

### Test Structure

```
tests/
├── conftest.py           # Shared fixtures: DB session, mock HTTP client, tmp paths
├── test_radarr.py        # Radarr API client unit tests
├── test_sonarr.py        # Sonarr API client unit tests
├── test_copier.py        # File copy / hardlink logic
├── test_scheduler.py     # Poll logic: deduplication, status transitions
├── test_watcher.py       # Share deletion → finished transition
└── test_api.py           # FastAPI route integration tests
```

### What to Test (per module)

**`test_radarr.py` / `test_sonarr.py`**
- Tag name resolves to correct tag ID.
- Movies/series without the configured tag are excluded.
- Items without a file on disk are excluded.
- API non-2xx response logs an error and returns empty list (does not raise).

**`test_copier.py`**
- File is copied to the correct destination path.
- Hardlink mode creates a hardlink instead of a copy.
- Destination directories are created if they don't exist.
- Copy failure sets status to `error` and records the exception message.

**`test_scheduler.py`**
- A new item is copied and recorded with status `copied`.
- An item already in DB with status `copied` is not re-copied (idempotency).
- An item with status `finished` is never re-copied.
- A failed copy records status `error`; next poll retries it.
- Concurrent poll trigger is skipped when a poll is already in progress.

**`test_watcher.py`**
- Deleting a file from the share that exists in DB as `copied` transitions it to `finished`.
- Deleting an untracked file has no side effects.

**`test_api.py`**
- `GET /health` returns `{"status": "ok"}` with HTTP 200.
- Config form saves values to DB and reloads correctly.
- Manual reset changes a `finished` item back to `pending`.

### Fixtures (conftest.py)

- `db_session` — in-memory SQLite session, tables created fresh per test.
- `mock_radarr` / `mock_sonarr` — pre-configured `respx` routers with sample API responses.
- `tmp_share` — `tmp_path`-based share directory with a sample media file pre-placed.
- Use `httpx.AsyncClient` for all API calls.
