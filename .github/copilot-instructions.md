# Copilot Instructions — Librarian

## Project Overview

**Librarian** is a self-hosted, single-Docker-container tool that fixes and standardises media library folder names in Radarr (movies) and Sonarr (TV shows). It detects folders whose names do not match the configured naming template, renames them on the mounted NFS share inside the container, and updates the Radarr/Sonarr database paths via their REST APIs — with no file moves performed by the arr apps themselves.

- **Movies** (Radarr): each movie folder is renamed to `{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}`.
- **TV Shows** (Sonarr): each series folder is renamed to `{Series TitleYear} {tvdb-{TvdbId}}` (i.e. `{Title} ({Year}) {tvdb-{TvdbId}}`).

The operator flow is: **Scan → Review → Approve → Apply** — no changes are applied without explicit approval.

---

## Goals & Constraints

- **Docker-only**: the entire application runs in a single Docker container. No docker-compose split. Everything — web server, rename engine — runs inside one image.
- **Everything is inside the container**: all application code, dependencies, and runtime live in the image. Nothing runs on the host directly.
- **No dev/prod split**: there is only one environment — the Docker container. No separate dev server, no environment-specific config files, no hot-reload tooling. What runs locally is identical to what runs in production.
- **Mount point directories are pre-created in the Dockerfile**: `/config`, `/media/movies`, and `/media/tv` are created with `mkdir -p` in the Dockerfile so the container starts cleanly even without volumes attached.
- **Shares are Docker volume mounts (read-write)**: the host binds external NFS paths into `/media/movies` (Radarr library) and `/media/tv` (Sonarr library). These mounts are **read-write** because Librarian renames folders in place.
- **Path remapping**: Radarr/Sonarr report folder paths using their own host-side root folders (e.g. `/movies/Dune.2021/`). Librarian strips this prefix (configured as `radarr_root_folder` / `sonarr_root_folder`) and prepends `/media/movies` or `/media/tv` to get the container-local path before renaming.
- **No external database**: SQLite only, stored at `/config/librarian.db` inside the container, persisted via the `/config` volume mount.
- **No JS build pipeline**: UI is server-rendered with Jinja2 templates + HTMX + Alpine.js. No React, Vue, or npm. JS assets (htmx, Alpine.js) are bundled into `app/static/js/` and served by FastAPI — no CDN dependencies.
- **No scheduler**: Librarian is triggered on-demand only. There are no background jobs, no cron, no APScheduler. The user clicks Scan; the user clicks Apply. Nothing happens automatically.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Strong ecosystem, async support, type hints |
| Web framework | FastAPI + Uvicorn | Async, typed, auto-docs |
| Templates | Jinja2 + HTMX + Alpine.js | Server-rendered, no JS build step; JS assets self-hosted under `app/static/js/` |
| Server-Sent Events | sse-starlette | Streaming live output to the UI during Apply |
| HTTP client | httpx (async) | Async HTTP for Radarr/Sonarr API calls |
| Database | SQLite via SQLModel | Type-safe ORM built on SQLAlchemy + Pydantic |
| Container | python:3.12-slim | Everything in one image |

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                   Docker Container                   │
│                                                      │
│  ┌──────────────────────────────────────────────┐    │
│  │   FastAPI Web UI (HTMX + Alpine.js)          │    │
│  │   - Scan trigger (on-demand)                 │    │
│  │   - Review page (approve / skip items)       │    │
│  │   - Apply trigger with SSE live output       │    │
│  │   - Settings page                            │    │
│  └───────────────────┬──────────────────────────┘    │
│                      │                               │
│         ┌────────────▼────────────┐                  │
│         │   SQLite (SQLModel)     │                  │
│         │   /config/librarian.db  │                  │
│         └────────────────────────┘                  │
│                                                      │
│  Volumes:                                            │
│    /config       → SQLite DB + app config            │
│    /media/movies → Radarr library share (read-write) │
│    /media/tv     → Sonarr library share (read-write) │
└──────────────────────────────────────────────────────┘
```

---

## Core Workflow

### Phase 1 — Scan
1. User selects **Radarr** or **Sonarr** in the UI and clicks **Scan**.
2. App fetches all movies (or series) from the arr API.
3. For each item, compute the expected folder name using the naming template.
4. Compare expected vs current folder name (basename of the `path` field in arr).
5. Items where current ≠ expected are written to the DB as `RenameItem` with `status=pending`.
6. Items already matching are ignored (no DB entry).
7. UI redirects to the Review page showing all pending items.

### Phase 2 — Review
- The Review page shows a table: **Current Name → Expected Name** for every pending item.
- User can toggle individual items (approve / skip).
- **Approve All** button marks everything as `approved`.
- **Skip** on a row marks that item as `skipped` (excluded from apply).
- User sets **batch size** (how many renames per apply run).

### Phase 3 — Apply
1. User clicks **Apply**.
2. App processes approved items in batches of `batch_size`.
3. For each item in the batch:
   a. Rename the folder on disk (container-local path via path remapping).
   b. Call `PUT /api/v3/movie/{id}` or `PUT /api/v3/series/{id}` with the new `path`.
   c. Update `RenameItem.status` to `done` or `error`.
4. Live output is streamed to the UI via SSE — each rename logs a line as it happens.
5. After all batches complete, a summary is shown (done / error counts).

### Item Status Values
```
pending → approved → done
        ↘ skipped           (excluded from apply; permanent for this scan)
pending → approved → error  (shown in UI)
```

- `pending` — detected mismatch, awaiting user decision
- `approved` — user approved, ready to apply
- `skipped` — user skipped, will not be renamed this run
- `done` — rename on disk and arr path update both succeeded
- `error` — rename or arr update failed; error message stored

### Error Handling During Apply
- **Disk rename fails**: log error, mark item `error`, skip arr update. Disk is unchanged.
- **Arr update fails after disk rename**: log error, mark item `error`. Disk has been renamed but arr still points at old path — the error message must make this clear.
- A single item error must never abort the entire batch — catch exceptions per-item and continue.

### Idempotency
- Running Scan again after a partial Apply is safe: items already `done` will not appear because their current folder name now matches the expected name.
- Re-scanning clears all non-`done` items for that source and rebuilds the list fresh.

---

## Folder Naming Rules

### Movies (Radarr)
Template: `{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}`

Fields from Radarr API (`GET /api/v3/movie`):
- `title` → apply `clean_title()` transformation
- `year` → release year
- `tmdbId` → TMDB ID

Example: `Avengers: Endgame (2019) {tmdb-299534}`

### TV Series (Sonarr)
Template: `{Series TitleYear} {tvdb-{TvdbId}}` = `{Title} ({Year}) {tvdb-{TvdbId}}`

Fields from Sonarr API (`GET /api/v3/series`):
- `title` → apply `clean_title()` transformation
- `year` → series start year
- `tvdbId` → TVDB ID

Example: `Breaking Bad (2008) {tvdb-81189}`

### CleanTitle Algorithm (`naming.py`)
The `clean_title(title: str) -> str` function makes a title safe for filesystem folder names:

1. Replace `: ` with ` - ` (colon-space → dash)
2. Replace standalone `:` with `-`
3. Remove characters: `? * " < > | \ /`
4. Collapse multiple consecutive spaces into one
5. Strip leading and trailing spaces and dots

Examples:
- `"Avengers: Endgame"` → `"Avengers - Endgame"`
- `"What We Do in the Shadows"` → `"What We Do in the Shadows"`
- `"S.W.A.T."` → `"S.W.A.T."`  *(dots are preserved)*

---

## Database Schema (SQLModel)

### `AppConfig`
Single-row configuration table (id always = 1).

| Column | Type | Default | Notes |
|---|---|---|---|
| id | int PK | 1 | always 1 |
| radarr_url | str | `""` | e.g. `http://192.168.1.10:7878` |
| radarr_api_key | str | `""` | |
| radarr_root_folder | str | `/movies` | Path prefix Radarr uses in folder paths |
| sonarr_url | str | `""` | e.g. `http://192.168.1.10:8989` |
| sonarr_api_key | str | `""` | |
| sonarr_root_folder | str | `/tv` | Path prefix Sonarr uses in folder paths |
| batch_size | int | `20` | Items per apply batch |
| created_at | datetime | now | |
| updated_at | datetime | now | |

### `ScanRun`
Represents a single Scan invocation.

| Column | Type | Notes |
|---|---|---|
| id | int PK | auto |
| source | str | `radarr` or `sonarr` |
| status | str | `scanning`, `ready`, `applying`, `done`, `error` |
| total_items | int | Total mismatches found |
| done_count | int | Successfully renamed so far |
| error_count | int | Errors so far |
| created_at | datetime | |
| updated_at | datetime | |

### `RenameItem`
One row per folder that needs renaming.

| Column | Type | Notes |
|---|---|---|
| id | int PK | auto |
| scan_run_id | int FK | FK → ScanRun.id |
| source | str | `radarr` or `sonarr` |
| source_id | int | Movie ID (Radarr) or Series ID (Sonarr) |
| title | str | Display title (e.g. `Avengers: Endgame`) |
| current_folder | str | Current folder name (basename) |
| expected_folder | str | Computed target folder name |
| current_path | str | Full path as stored in arr (arr's view) |
| expected_path | str | Full expected path (arr's view) |
| status | str | `pending`, `approved`, `skipped`, `done`, `error` |
| error_message | str \| None | Error detail if status = error |
| created_at | datetime | |
| updated_at | datetime | |

---

## Project Structure

```
librarian/
├── Dockerfile
├── requirements.txt
├── pytest.ini
├── docs/                  # Design and reference documentation
├── todo/                  # Implementation task files (one per module)
├── app/
│   ├── main.py            # FastAPI app, startup lifespan
│   ├── config.py          # Settings loader (env vars + DB)
│   ├── database.py        # SQLite engine, session factory
│   ├── models.py          # SQLModel table definitions (AppConfig, ScanRun, RenameItem)
│   ├── naming.py          # clean_title() + folder name computation for movies and TV
│   ├── arr_client.py      # Base ArrClient: shared httpx auth + request helpers
│   ├── radarr.py          # RadarrClient: fetch movies, update movie path
│   ├── sonarr.py          # SonarrClient: fetch series, update series path
│   ├── scanner.py         # Scan logic: fetch items, compute mismatches, write RenameItems
│   ├── renamer.py         # Apply logic: rename on disk, call arr PUT, update DB status
│   ├── log_buffer.py      # In-memory deque of recent log lines; feeds SSE stream
│   ├── routers/
│   │   ├── ui.py          # Jinja2 page routes (scan, review, apply, settings)
│   │   └── api.py         # REST API routes (scan trigger, approve, apply, SSE stream)
│   ├── static/
│   │   └── js/
│   │       ├── htmx.min.js    # htmx (self-hosted, no CDN)
│   │       └── alpine.min.js  # Alpine.js (self-hosted, no CDN)
│   └── templates/
│       ├── base.html
│       ├── dashboard.html     # Source picker + last scan summary
│       ├── review.html        # Mismatch table: approve/skip, batch size, Apply button
│       ├── settings.html      # API config form
│       └── logs.html          # SSE live output during Apply
└── tests/
    ├── conftest.py
    ├── test_naming.py
    ├── test_radarr.py
    ├── test_sonarr.py
    ├── test_scanner.py
    ├── test_renamer.py
    └── test_api.py
```

---

## Docker Details

- Base image: `python:3.12-slim`
- Exposed port: `8080`
- Mount point directories **pre-created in the Dockerfile** (`mkdir -p`):
  - `/config` — SQLite DB (`librarian.db`) persisted via volume mount
  - `/media/movies` — read-write mount for Radarr library (folder renames happen here)
  - `/media/tv` — read-write mount for Sonarr library (folder renames happen here)

```dockerfile
RUN mkdir -p /config /media/movies /media/tv
```

- Environment variables (all optional; can also be set via Settings UI):
  - `RADARR_URL`, `RADARR_API_KEY`, `RADARR_ROOT_FOLDER` (default: `/movies`)
  - `SONARR_URL`, `SONARR_API_KEY`, `SONARR_ROOT_FOLDER` (default: `/tv`)
  - `BATCH_SIZE` (default: `20`)
  - `TZ` (default: `UTC`)

### Run the app
```powershell
docker run -d `
  -p 8080:8080 `
  -v /path/to/config:/config `
  -v /path/to/movies:/media/movies `
  -v /path/to/tv:/media/tv `
  --name librarian `
  librarian
```

---

## UI Pages

The UI uses a **mixed light/dark theme**: dark nav bar (`#1e293b`), light page body (`#f1f5f9`), white cards with subtle shadows. No external CSS framework — all styling is inline in `base.html`.

| Route | Description |
|---|---|
| `/` | Dashboard: pick source (Radarr / Sonarr), last scan summary card, Scan button |
| `/review` | Mismatch table (current → expected), approve/skip toggles, Approve All, batch size input, Apply button |
| `/apply` | SSE live output log during apply; auto-scrolls; shows summary when done |
| `/settings` | Radarr URL + key, Sonarr URL + key, root folder paths, batch size; Save button |
| `/logs` | Recent log output (last N lines from log_buffer); Clear button |

---

## Radarr / Sonarr API Reference

### Radarr
| Operation | Endpoint |
|---|---|
| Fetch all movies | `GET /api/v3/movie` |
| Update movie path | `PUT /api/v3/movie/{id}` — send full movie object with updated `path` |

The `path` field in the movie object is the **folder path** (not a file path). Example: `/movies/Dune (2021) {tmdb-438631}`.

### Sonarr
| Operation | Endpoint |
|---|---|
| Fetch all series | `GET /api/v3/series` |
| Update series path | `PUT /api/v3/series/{id}` — send full series object with updated `path` |

The `path` field is the series folder. Example: `/tv/Breaking Bad (2008) {tvdb-81189}`.

### Shared
- Authentication: `X-Api-Key: <key>` header on all requests.
- Always GET the full object before PUT — never construct a partial body.
- A `PUT` with `moveFiles=false` (default) updates only the DB path; no physical move is triggered by arr.
- Use `httpx.AsyncClient` for all API calls.

### Path Remapping
```
arr path:        /movies/Dune.2021.2160p/
container path:  /media/movies/Dune.2021.2160p/   (radarr_root_folder=/movies)

arr path:        /tv/Breaking.Bad.S01/
container path:  /media/tv/Breaking.Bad.S01/       (sonarr_root_folder=/tv)
```
`remap_to_container(arr_path, root_folder, media_path)` in `renamer.py` performs this translation.

---

## Instructions File Maintenance

- **Always update `.github/copilot-instructions.md`** as part of any change that affects architecture, schema, module behaviour, UI, or workflow — before committing.
- The instructions file is the authoritative reference for how the project works. It must reflect the current implementation at all times.
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

1. **Build** — `docker build -t librarian .`
2. **Test** — `docker run --rm librarian pytest -v`
3. **Commit** — if all tests pass:
   ```powershell
   git add -A; git commit -m "<short description of what changed>"
   ```

- Never leave uncommitted changes after a successful build+test cycle.
- Commit messages: imperative, present-tense, specific (e.g. `Add clean_title function`, `Implement batch apply with SSE output`).

---

## Coding Standards

- Use **type hints** everywhere.
- Use **async/await** for all I/O (HTTP calls via `httpx`; disk rename via `asyncio.to_thread`).
- Keep each module focused — no business logic in route handlers.
- Log with Python's `logging` module: INFO for normal flow, WARNING for skips, ERROR for failures.
- All configuration must be readable from environment variables on startup, with the DB config row as the authoritative source at runtime.
- API keys must **never** appear in log output.

### Code Comments & Clarity
- Every module must have a top-of-file docstring explaining what it does and why.
- Every function/method must have a short docstring.
- Use inline comments for non-obvious logic — explain the *why*, not the *what*.

### Code Reuse & DRY
- `naming.py` is the single source of truth for folder name computation — never inline naming logic elsewhere.
- `renamer.py` is the single source of truth for disk operations — never inline `os.rename` outside it.
- Radarr and Sonarr clients share a `BaseArrClient` in `arr_client.py`.
- DB query patterns that repeat must be named functions, not ad-hoc inline queries.





---

## Error Handling

- If a Radarr/Sonarr API call fails (network error, non-2xx), log the error and surface it in the UI — do not crash.
- If a disk rename fails, mark the item `error`, log the exception, and continue with the next item.
- If arr path update fails after a successful disk rename, mark the item `error` and clearly log that the folder *has* been renamed on disk but arr was not updated.
- API key must never appear in log output.

---

## Graceful Shutdown

- On container SIGTERM, FastAPI's lifespan context must cleanly close the SQLite connection pool.

---

## Health Check

- Expose `GET /health` returning `{"status": "ok"}` with HTTP 200.
- Used by Docker's `HEALTHCHECK` directive to confirm the app is running.
- Add to Dockerfile: `HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1`

---

## Testing Guidelines

- Use `pytest` + `pytest-asyncio` for all tests.
- **Never** call real Radarr/Sonarr APIs in tests — mock `httpx.AsyncClient` with `respx` or `pytest-httpx`.
- Use a temporary in-memory SQLite DB for all DB tests (`sqlite:///:memory:`).
- Test disk renames with a `tmp_path` fixture — no real NFS share needed.

### What to Test (per module)

**`test_naming.py`**
- `clean_title()` handles colons, question marks, slashes, trailing dots correctly.
- Movie folder name computed correctly from title + year + tmdbId.
- Series folder name computed correctly from title + year + tvdbId.

**`test_radarr.py` / `test_sonarr.py`**
- Fetch all movies/series returns expected list.
- `PUT` update sends full object with modified path.
- Non-2xx response logs error and raises / returns sentinel.

**`test_scanner.py`**
- Items already matching expected name are excluded.
- Mismatches are written to DB as `pending`.
- Re-scan clears existing non-done items and rebuilds.

**`test_renamer.py`**
- Folder renamed on disk to correct new name.
- Arr `PUT` called with new path after successful disk rename.
- Disk rename failure marks item `error`, skips arr call.
- Arr update failure after disk rename marks item `error` with clear message.

**`test_api.py`**
- `GET /health` returns `{"status": "ok"}` with HTTP 200.
- Approve endpoint changes item status to `approved`.
- Skip endpoint changes item status to `skipped`.

### Fixtures (conftest.py)

- `db_session` — in-memory SQLite session, tables created fresh per test.
- `mock_radarr` / `mock_sonarr` — pre-configured `respx` routers with sample API responses.
- `tmp_media` — `tmp_path`-based library directory with a sample folder pre-created.
