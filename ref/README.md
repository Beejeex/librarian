# MadTracked

A self-hosted, single-container tool that monitors **Radarr** and **Sonarr** for tagged media items, copies their files to a configured share, and automatically marks them as finished when the copied file is deleted from the share.

> **Current version: v0.1.0**

---

## How It Works

1. **Poll** Radarr/Sonarr on a configurable interval (default: 15 min).
2. Any movie or TV episode file that has the configured tag applied is **copied** to the share.
3. A **file watcher + poll-time reconciliation** monitors the share — when a copied file is deleted, the item is automatically marked as **finished**.
4. Items marked finished are never re-copied, keeping the share clean and intentional.

**Movies** (Radarr) are tracked as a single item per movie.
**TV Shows** (Sonarr) are tracked at the episode file level — every episode file is individually copied and tracked, preserving the `Series/Season XX/` folder structure.

**Subtitle files** (`.srt`, `.sub`, `.ass`, `.ssa`, `.vtt`, `.sup`) are automatically copied alongside the video file when found.

---

## Features

- Single Docker container — no external database, no separate services
- Web UI with dashboard, item table, config form, and live log tail
- **Live copy progress** — per-file speed and percentage shown in the dashboard while copying
- **Quota management** — configurable size (GB) and file count caps with a 60/40 backlog/new split
- **Approval workflow** — optional require-approval mode; first-run always queues everything as backlog for review
- **Per-source first-run** — Radarr and Sonarr index independently; each only activates when its tags are configured
- **Subtitle copying** — companion subtitle files are copied alongside the main video
- **Items search** — filter by status and search by title on the Items page
- Path remapping between Radarr/Sonarr host paths and container mount points
- Multi-tag support — comma-separated tag names; any match includes the item
- SQLite state persistence via `/config` volume mount
- Version displayed in the nav bar

---

## Quick Start

```bash
docker run -d \
  -p 8080:8080 \
  -v /path/to/config:/config \
  -v /path/to/movies:/media/movies:ro \
  -v /path/to/tv:/media/tv:ro \
  -v /path/to/share:/share \
  --name madtracked \
  ghcr.io/beejeex/madtracked:v0.1.0
```

Then open **http://localhost:8080** and configure your Radarr/Sonarr connection via the Config page.

---

## Volume Mounts

| Mount | Mode | Purpose |
|---|---|---|
| `/config` | read-write | SQLite database (`madtracked.db`) |
| `/media/movies` | read-only | Radarr source files |
| `/media/tv` | read-only | Sonarr source files |
| `/share` | read-write | Destination share |

---

## Environment Variables

All settings can also be configured via the web UI. Environment variables set the initial values on first run.

| Variable | Default | Description |
|---|---|---|
| `RADARR_URL` | — | e.g. `http://192.168.1.10:7878` |
| `RADARR_API_KEY` | — | Radarr API key |
| `RADARR_TAGS` | — | Comma-separated tag names to watch in Radarr |
| `RADARR_ROOT_FOLDER` | `/movies` | Path prefix Radarr uses in file paths |
| `SONARR_URL` | — | e.g. `http://192.168.1.10:8989` |
| `SONARR_API_KEY` | — | Sonarr API key |
| `SONARR_TAGS` | — | Comma-separated tag names to watch in Sonarr |
| `SONARR_ROOT_FOLDER` | `/tv` | Path prefix Sonarr uses in file paths |
| `POLL_INTERVAL_MINUTES` | `15` | How often to poll Radarr/Sonarr |
| `REQUIRE_APPROVAL` | `false` | Require manual approval for new (non-backlog) items |
| `MAX_CONCURRENT_COPIES` | `2` | Max parallel file copies per poll |
| `MAX_SHARE_SIZE_GB` | `0` | Max share size in GB (`0` = unlimited) |
| `MAX_SHARE_FILES` | `0` | Max files on share (`0` = unlimited) |
| `TZ` | `UTC` | Container timezone |

---

## Quota System

When limits are set, the quota is split between backlog and new items:

| Pool | Cap |
|---|---|
| Backlog items (first-run indexed) | 60% of each limit |
| New items (discovered after first run) | 40% of each limit |

Set both `MAX_SHARE_SIZE_GB` and `MAX_SHARE_FILES` to `0` to disable quota enforcement entirely.

---

## Path Remapping

Radarr and Sonarr report file paths as they exist on their own host (e.g. `/movies/Film/file.mkv`). MadTracked remaps these to the container's mount points:

```
/movies/Film/file.mkv   ->  /media/movies/Film/file.mkv   (RADARR_ROOT_FOLDER=/movies)
/tv/Show/S01/ep.mkv     ->  /media/tv/Show/S01/ep.mkv     (SONARR_ROOT_FOLDER=/tv)
```

Configure `RADARR_ROOT_FOLDER` / `SONARR_ROOT_FOLDER` to match whatever root folder path your Radarr/Sonarr instances use.

---

## Status Lifecycle

```
queued -> pending -> copying -> copied -> finished
               \-> error  (retried on next poll)
queued -> finished  (skip)
```

| Status | Meaning |
|---|---|
| `queued` | Discovered; awaiting approval |
| `pending` | Approved; waiting for quota / semaphore slot |
| `copying` | Actively being copied |
| `copied` | File is on the share |
| `finished` | File deleted from share (done) or manually skipped |
| `error` | Copy failed; retried automatically on next poll |

---

## UI Pages

| Route | Description |
|---|---|
| `/` | Dashboard — stat cards, live copy progress, share usage, recent activity |
| `/items` | Full item table with status filter, title search, and action buttons |
| `/config` | Connection settings, tag selection, quota limits, approval options |
| `/logs` | Tail of recent log output |

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Web framework | FastAPI + Uvicorn |
| Templates | Jinja2 + HTMX + Alpine.js (self-hosted) |
| Scheduler | APScheduler |
| File watcher | watchdog |
| Database | SQLite via SQLModel |
| Container | python:3.12-slim |

---

## Health Check

```
GET /health  ->  {"status": "ok"}
```

---

## License

MIT
