# Librarian — Architecture

## Container Overview

```
┌──────────────────────────────────────────────────────────┐
│                     Docker Container                     │
│                                                          │
│   ┌────────────────────────────────────────────────┐     │
│   │   FastAPI (Uvicorn)                            │     │
│   │                                                │     │
│   │   Routers:                                     │     │
│   │     /           → ui.py  (dashboard)           │     │
│   │     /review     → ui.py  (review table)        │     │
│   │     /apply      → ui.py  (SSE log page)        │     │
│   │     /settings   → ui.py  (config form)         │     │
│   │     /logs       → ui.py  (log viewer)          │     │
│   │     /api/*      → api.py (JSON + SSE endpoints)│     │
│   │     /health     → api.py (health check)        │     │
│   │                                                │     │
│   │   Templates: Jinja2 + HTMX + Alpine.js         │     │
│   │   Static JS: htmx.min.js, alpine.min.js        │     │
│   └────────────────────┬───────────────────────────┘     │
│                        │                                 │
│          ┌─────────────▼─────────────┐                   │
│          │   SQLite — librarian.db   │                   │
│          │   (SQLModel / SQLAlchemy) │                   │
│          │                           │                   │
│          │   Tables:                 │                   │
│          │   - AppConfig (1 row)     │                   │
│          │   - ScanRun               │                   │
│          │   - RenameItem            │                   │
│          └─────────────┬─────────────┘                   │
│                        │                                 │
│          ┌─────────────▼─────────────┐                   │
│          │   Business Logic          │                   │
│          │   - scanner.py            │                   │
│          │   - renamer.py            │                   │
│          │   - naming.py             │                   │
│          │   - radarr.py             │                   │
│          │   - sonarr.py             │                   │
│          │   - arr_client.py (base)  │                   │
│          │   - log_buffer.py         │                   │
│          └─────────────┬─────────────┘                   │
│                        │                                 │
│      ┌─────────────────┴──────────────────┐              │
│      │                                    │              │
│  ┌───▼────────────────┐   ┌───────────────▼──────────┐  │
│  │  httpx.AsyncClient │   │  os.rename() via          │  │
│  │  (Radarr/Sonarr    │   │  asyncio.to_thread        │  │
│  │   REST API)        │   │  (NFS folder rename)      │  │
│  └────────────────────┘   └──────────────────────────┘  │
│                                                          │
│  Volumes:                                                │
│    /config          SQLite DB persisted here             │
│    /media/movies    Radarr NFS share (read-write)        │
│    /media/tv        Sonarr NFS share (read-write)        │
└──────────────────────────────────────────────────────────┘
```

---

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app factory, lifespan (DB init, graceful shutdown), router registration |
| `config.py` | Load `AppConfig`: env vars on startup, DB row at runtime |
| `database.py` | SQLite engine creation, session factory, table creation |
| `models.py` | SQLModel table definitions: `AppConfig`, `ScanRun`, `RenameItem` |
| `naming.py` | `clean_title()`, `movie_folder_name()`, `series_folder_name()` |
| `arr_client.py` | `BaseArrClient`: httpx session, `X-Api-Key` header, `get()`, `put()` helpers |
| `radarr.py` | `RadarrClient(BaseArrClient)`: `fetch_movies()`, `update_movie_path()` |
| `sonarr.py` | `SonarrClient(BaseArrClient)`: `fetch_series()`, `update_series_path()` |
| `scanner.py` | `run_scan()`: fetch → compute → compare → write `RenameItem` rows |
| `renamer.py` | `run_apply()`: rename disk folders, call arr PUT, update DB status, emit SSE events |
| `log_buffer.py` | Thread-safe in-memory `deque` of recent log lines; `append()` and `tail()` |
| `routers/ui.py` | Jinja2 HTML page routes |
| `routers/api.py` | JSON API routes + SSE `/api/stream` endpoint |

---

## Data Flow — Scan

```
ui.py: POST /api/scan?source=radarr
    │
    ▼
api.py: trigger_scan(source)
    │
    ▼
scanner.py: run_scan(source, db, config)
    ├── RadarrClient.fetch_movies()          GET /api/v3/movie
    ├── for each movie:
    │       naming.movie_folder_name(movie)  → expected
    │       basename(movie['path'])          → current
    │       if current != expected:
    │           write RenameItem(status=pending)
    └── update ScanRun(status=ready)
    │
    ▼
ui.py: redirect to /review
```

---

## Data Flow — Apply

```
ui.py: POST /api/apply
    │
    ▼
api.py: trigger_apply(batch_size, db, config)   (runs in background task)
    │
    ▼
renamer.py: run_apply(scan_run_id, batch_size, db, config)
    │
    ├── load approved RenameItems in batches
    │
    └── for each item:
        ├── remap_to_container(item.current_path) → local disk path
        ├── asyncio.to_thread(os.rename, old_path, new_path)
        │       on fail: item.status=error, log_buffer.append(err msg), continue
        ├── GET /api/v3/movie/{id}                → full object
        ├── modify object['path'] = item.expected_path
        ├── PUT /api/v3/movie/{id}                → updated object
        │       on fail: item.status=error, log_buffer.append(warn msg), continue
        └── item.status=done, log_buffer.append(ok msg)

SSE stream:
    browser ←── GET /api/stream ←── log_buffer.tail() (polling deque)
```

---

## Tech Stack

| Layer | Technology | Version |
|---|---|---|
| Language | Python | 3.12 |
| Web framework | FastAPI + Uvicorn | latest stable |
| Templates | Jinja2 | latest stable |
| Frontend | HTMX + Alpine.js | self-hosted JS bundles |
| SSE | sse-starlette | latest stable |
| HTTP client | httpx | latest stable (async) |
| ORM / DB | SQLModel + SQLite | latest stable |
| Tests | pytest + pytest-asyncio + respx | latest stable |

No APScheduler, no watchdog, no Celery, no Redis, no external message broker.

---

## Configuration Loading

Configuration has two layers:

1. **Environment variables** (read once at startup): used to populate the `AppConfig` DB row if it doesn't exist yet, or to override specific fields on every startup.
2. **DB row** (`AppConfig`, id=1): the authoritative runtime source. The Settings UI writes to this row. Code always reads from the DB row at request time, not from env vars.

### Environment variable → DB field mapping

| Env var | DB field | Default |
|---|---|---|
| `RADARR_URL` | `radarr_url` | `""` |
| `RADARR_API_KEY` | `radarr_api_key` | `""` |
| `RADARR_ROOT_FOLDER` | `radarr_root_folder` | `/movies` |
| `SONARR_URL` | `sonarr_url` | `""` |
| `SONARR_API_KEY` | `sonarr_api_key` | `""` |
| `SONARR_ROOT_FOLDER` | `sonarr_root_folder` | `/tv` |
| `BATCH_SIZE` | `batch_size` | `20` |

---

## SSE Live Output

The apply process streams log lines to the UI using **Server-Sent Events** (SSE).

```
Browser                    FastAPI
  │                            │
  │── GET /api/stream ────────►│
  │                            │  (keeps connection open)
  │◄── data: ✔ Dune renamed ──│
  │◄── data: ✔ Lucifer...  ───│
  │◄── data: [DONE] ──────────│
  │                            │  (stream closes)
  │── connection closed ───────│
```

`log_buffer.py` provides a thread-safe `deque` (max 500 lines). The SSE endpoint polls this deque and yields new lines as SSE events. The browser auto-scrolls the log `<pre>` container using Alpine.js.

---

## No Scheduler Design

Librarian deliberately has **no background scheduler**.

- No APScheduler, no cron, no asyncio periodic tasks.
- The only async work happens when the operator triggers a scan or an apply.
- The FastAPI lifespan only needs to handle: DB table creation on startup, connection pool teardown on shutdown.
- This keeps the app simple, predictable, and easy to reason about.
