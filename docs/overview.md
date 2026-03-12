# Librarian — Project Overview

## What is Librarian?

Librarian is a self-hosted tool that standardises media library folder names for **Radarr** (movies) and **Sonarr** (TV shows). It:

1. Detects folders whose names do not match Radarr's/Sonarr's configured naming template.
2. Presents a list for operator review.
3. On approval, **renames the folders on the NFS share** and updates the arr application database paths via the REST API — no physical file moves are performed by arr itself.

Librarian runs entirely inside a single Docker container. No scheduler, no background jobs — everything is triggered on-demand by the operator.

---

## Why Librarian Exists

Radarr and Sonarr manage media libraries but do not automatically rename folders that were added before a naming template was configured, or folders whose names were set by external tools. Renaming folders manually and then updating arr's database is tedious at scale.

Librarian automates this:
- Fetch all items from arr.
- Compute what the folder name *should* be based on the naming template.
- Show only the items that don't already match.
- Let the operator approve and apply in controlled batches.

---

## What Librarian Does NOT Do

- Does **not** move files between root folders.
- Does **not** rename individual media files (only folders).
- Does **not** run on a schedule — every scan and apply is triggered manually.
- Does **not** manage Lidarr, Readarr, or any arr other than Radarr and Sonarr.
- Does **not** have a CLI mode — the interface is web-only.

---

## Operator Flow

```
[Dashboard]
    │
    ├─ Pick source: Radarr or Sonarr
    └─ Click "Scan"
           │
           ▼
    [Scan runs]
    Fetch all items from arr API
    Compute expected folder name
    Record mismatches in DB (status=pending)
           │
           ▼
    [Review page]
    Table: Current Name → Expected Name
    Toggle approve / skip per row
    Set batch size
    Click "Approve All" or approve individually
           │
           ▼
    [Apply]
    Process approved items in batches
    ├─ Rename folder on disk (NFS share)
    └─ Update arr DB path via PUT
    Live output streamed to UI via SSE
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Rename on disk + update arr | Arr's native rename moves files, which is slow. Librarian renames only the folder (folder already has content inside), then tells arr the new path. |
| On-demand only, no scheduler | Folder renames are a one-off cleanup operation, not something that should run continuously. |
| Collect → Approve → Apply | Gives the operator full visibility and control before anything changes on disk. |
| Batch size setting | Allows gradual processing; easier to recover if something goes wrong part-way through. |
| SSE live output | Operator can see each rename happening in real time without polling. |
| Single Docker container | Simple deployment; no orchestration needed. |

---

## Docker Volumes

| Mount | Purpose | Access |
|---|---|---|
| `/config` | SQLite database (`librarian.db`) | read-write |
| `/media/movies` | Radarr library (NFS share) | **read-write** (folder renames) |
| `/media/tv` | Sonarr library (NFS share) | **read-write** (folder renames) |

The media mounts must be **read-write** — Librarian calls `os.rename()` on these paths.
