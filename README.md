# Librarian

Self-hosted, single-container tool with two integrated functions:

- **Renamer** — detects Radarr/Sonarr library folders whose names don't match the configured naming template, renames them on the mounted share, and updates the arr database paths via their REST APIs. No file moves are performed by the arr apps themselves.
- **Tracker** — monitors Radarr/Sonarr for tagged media, copies their files to a dedicated share (`/share`), and marks items as finished when the copied file is deleted from the share.

| Function | Flow |
|---|---|
| Renamer | Scan → Review → Approve → Apply |
| Tracker | Poll (automatic) → Review queued → Approve → Copy |

---

## Quick start

```bash
docker run -d \
  -p 8080:8080 \
  -v /path/to/config:/config \
  -v /path/to/movies:/media/movies \
  -v /path/to/tv:/media/tv \
  -v /path/to/share:/share \
  --name librarian \
  ghcr.io/beejeex/librarian:latest
```

Then open **http://your-host:8080** and configure Radarr/Sonarr under **Settings**.

---

## Volume mounts

| Mount | Purpose | Access |
|---|---|---|
| `/config` | SQLite database (`librarian.db`) | read-write |
| `/media/movies` | Radarr library share | read-write |
| `/media/tv` | Sonarr library share | read-write |
| `/share` | Tracker copy destination | read-write |

The `/media/movies` and `/media/tv` mounts must point to the **same share** that Radarr/Sonarr use, so Librarian can rename folders in place. `/share` is a separate destination used only by the Tracker.

---

## Environment variables

All settings can also be configured via the **Settings** page in the UI.

### Renamer

| Variable | Default | Description |
|---|---|---|
| `RADARR_URL` | *(empty)* | Radarr base URL, e.g. `http://192.168.1.10:7878` |
| `RADARR_API_KEY` | *(empty)* | Radarr API key |
| `RADARR_ROOT_FOLDER` | `/movies` | Path prefix Radarr uses internally |
| `SONARR_URL` | *(empty)* | Sonarr base URL, e.g. `http://192.168.1.10:8989` |
| `SONARR_API_KEY` | *(empty)* | Sonarr API key |
| `SONARR_ROOT_FOLDER` | `/tv` | Path prefix Sonarr uses internally |
| `BATCH_SIZE` | `20` | Folders renamed per apply run |

### Tracker

| Variable | Default | Description |
|---|---|---|
| `RADARR_TAGS` | *(empty)* | Comma-separated Radarr tag names to track |
| `SONARR_TAGS` | *(empty)* | Comma-separated Sonarr tag names to track |
| `POLL_INTERVAL_MINUTES` | `15` | How often the tracker polls for new items |
| `REQUIRE_APPROVAL` | `false` | If `true`, new items land as `queued` and require manual approval before copying |
| `MAX_CONCURRENT_COPIES` | `2` | Parallel copy operations per poll cycle |
| `MAX_SHARE_SIZE_GB` | `0` | Total size cap on `/share` in GB; `0` = unlimited |
| `MAX_SHARE_FILES` | `0` | Total file count cap on `/share`; `0` = unlimited |
| `SHARE_PATH` | `/share` | Copy destination root inside the container |

### Notifications (ntfy.sh)

| Variable | Default | Description |
|---|---|---|
| `NTFY_URL` | `https://ntfy.sh` | ntfy server URL |
| `NTFY_TOPIC` | *(empty)* | Topic to publish to; empty = notifications disabled |
| `NTFY_TOKEN` | *(empty)* | Optional Bearer token for authenticated topics |

### General

| Variable | Default | Description |
|---|---|---|
| `TZ` | `UTC` | Container timezone |

---

## Path remapping

Radarr/Sonarr report paths using their own host-side root folder. Librarian strips that prefix (configured as **Root Folder** in Settings) and prepends the container mount path.

```
Radarr reports:      /movies/Dune.2021.2160p
Root Folder setting: /movies
Container sees:      /media/movies/Dune.2021.2160p
```

Make sure **Root Folder** in Librarian's Settings matches the root folder configured in Radarr/Sonarr.

---

## Unraid / docker-compose example

```yaml
services:
  librarian:
    image: ghcr.io/beejeex/librarian:latest
    container_name: librarian
    ports:
      - "8080:8080"
    volumes:
      - /mnt/user/appdata/librarian:/config
      - /mnt/user/media/movies:/media/movies
      - /mnt/user/media/tv:/media/tv
      - /mnt/user/share:/share
    environment:
      - RADARR_URL=http://192.168.1.10:7878
      - RADARR_API_KEY=your-radarr-api-key
      - RADARR_ROOT_FOLDER=/movies
      - SONARR_URL=http://192.168.1.10:8989
      - SONARR_API_KEY=your-sonarr-api-key
      - SONARR_ROOT_FOLDER=/tv
      - RADARR_TAGS=tracked
      - SONARR_TAGS=tracked
      - POLL_INTERVAL_MINUTES=15
      - TZ=Europe/Amsterdam
    restart: unless-stopped
```

---

## Usage

### Renamer

**1 — Settings**
Go to **Settings → Librarian** and enter your Radarr/Sonarr URL, API key, and root folder path. Save.

**2 — Scan**
On the **Renamer** page, click **Scan Radarr** or **Scan Sonarr**. Librarian fetches all items, computes which folder names don't match the expected template, and queues mismatches for review.

**3 — Review**
The table shows current name → expected name with a scenario badge per row. You can approve or skip individual items, or click **Approve All**.

**4 — Apply**
Click **Apply**. Folders are renamed and arr paths updated via the API. Live output is streamed to the screen.

---

### Tracker

**1 — Settings**
Go to **Settings → Tracker** and enter the tag names you use in Radarr/Sonarr to mark items for tracking.

**2 — First run**
On the first poll, all currently-tagged items are indexed as `queued` backlog — nothing is copied yet. Review them on the **Items** page and approve the ones you want copied.

**3 — Ongoing polling**
The scheduler runs every `POLL_INTERVAL_MINUTES`. Approved items are copied to `/share`. When a file is deleted from `/share`, it is automatically marked `finished`.

---

## Renamer item statuses

| Status | Meaning |
|---|---|
| `pending` | Detected mismatch, awaiting your decision |
| `approved` | Approved for rename |
| `skipped` | Excluded from this apply run |
| `done` | Folder renamed and arr path updated |
| `error` | Rename or arr update failed — see error message |

## Tracker item statuses

| Status | Meaning |
|---|---|
| `queued` | Discovered, awaiting approval |
| `pending` | Approved, waiting for the next poll to copy |
| `copied` | File present on `/share` |
| `finished` | File was deleted from `/share` |
| `error` | Copy failed — will retry on next poll |

---

## Building from source

```powershell
git clone https://github.com/Beejeex/librarian.git
cd librarian
docker build -t librarian .
docker run --rm librarian pytest -v
```

---

## Health check

`GET /health` returns `{"status": "ok"}` with HTTP 200. Used by Docker's built-in `HEALTHCHECK`.
