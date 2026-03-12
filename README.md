# Librarian

Self-hosted tool that fixes and standardises media library folder names in **Radarr** and **Sonarr**.

It detects folders whose names don't match the configured naming template, renames them on the mounted NFS share, and updates the Radarr/Sonarr database paths via their REST APIs — with no file moves performed by the arr apps themselves.

| Source | Template |
|---|---|
| Radarr | `{CleanTitle} ({Year}) {tmdb-{TmdbId}}` |
| Sonarr | `{Title} ({Year}) {tvdb-{TvdbId}}` |

**Operator flow:** Scan → Review → Approve → Apply

---

## Quick start

```powershell
docker run -d \
  -p 8080:8080 \
  -v /path/to/config:/config \
  -v /path/to/movies:/media/movies \
  -v /path/to/tv:/media/tv \
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

The `/media/movies` and `/media/tv` paths must point to the **same NFS share** that Radarr/Sonarr use, so Librarian can rename folders in place.

---

## Environment variables

All settings can also be configured via the **Settings** page in the UI.

| Variable | Default | Description |
|---|---|---|
| `RADARR_URL` | *(empty)* | Radarr base URL, e.g. `http://192.168.1.10:7878` |
| `RADARR_API_KEY` | *(empty)* | Radarr API key |
| `RADARR_ROOT_FOLDER` | `/movies` | Path prefix Radarr uses internally |
| `SONARR_URL` | *(empty)* | Sonarr base URL, e.g. `http://192.168.1.10:8989` |
| `SONARR_API_KEY` | *(empty)* | Sonarr API key |
| `SONARR_ROOT_FOLDER` | `/tv` | Path prefix Sonarr uses internally |
| `BATCH_SIZE` | `20` | Folders renamed per apply run |
| `TZ` | `UTC` | Container timezone |

---

## Path remapping

Radarr/Sonarr report folder paths using their own host-side root folder (e.g. `/movies/Dune.2021/`). Librarian strips this prefix (configured as **Root Folder** in Settings) and prepends the container mount path to get the local path before renaming.

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
    environment:
      - RADARR_URL=http://192.168.1.10:7878
      - RADARR_API_KEY=your-radarr-api-key
      - RADARR_ROOT_FOLDER=/movies
      - SONARR_URL=http://192.168.1.10:8989
      - SONARR_API_KEY=your-sonarr-api-key
      - SONARR_ROOT_FOLDER=/tv
      - TZ=Europe/Amsterdam
    restart: unless-stopped
```

---

## Usage

### 1 — Settings
Go to **Settings** and enter your Radarr and/or Sonarr URL, API key, and root folder path. Save.

### 2 — Scan
On the **Dashboard**, click **Scan Radarr** or **Scan Sonarr**. Librarian fetches all items from the arr API, computes which folder names don't match the expected template, and queues up to **`BATCH_SIZE`** mismatches for review.

If more mismatches exist beyond the batch size, run Scan again after applying the current batch — already-renamed folders are excluded automatically, so each scan picks up the next batch of work.

### 3 — Review
The **Review** page shows a table of mismatches: current name → expected name. You can:
- **Approve** individual items or click **Approve All**
- **Skip** items you want to leave unchanged

### 4 — Apply
Set the **batch size** and click **Apply**. Librarian renames folders in batches and updates the arr database path via the API. Live output is streamed to the screen as it happens.

---

## Item statuses

| Status | Meaning |
|---|---|
| `pending` | Detected mismatch, awaiting your decision |
| `approved` | Approved for rename |
| `skipped` | Excluded from this apply run |
| `done` | Folder renamed and arr path updated |
| `error` | Rename or arr update failed — see error message |

If an arr path update fails **after** the disk rename succeeds, the error message will explicitly say so — you'll need to update the path manually in the arr UI for that item.

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
