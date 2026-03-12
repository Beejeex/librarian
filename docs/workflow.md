# Librarian — Workflow

## Overview

The operator workflow has three phases: **Scan → Review → Apply**.

Nothing is changed on disk or in arr until the operator explicitly clicks Apply. Every step is visible and reversible (by skipping or abandoning the scan run).

```
[Dashboard]
     │
     ├── Pick source: Radarr | Sonarr
     └── Click "Scan"
               │
               ▼
          [Phase 1: Scan]
          Fetch all items from arr API
          Compute expected folder name
          Compare vs current folder name
          Write mismatches to DB (status=pending)
          Redirect to Review page
               │
               ▼
          [Phase 2: Review]
          Table: Current Name → Expected Name
          User approves / skips individual rows
          "Approve All" to mark everything
          Set batch size (default 20)
          Click "Apply"
               │
               ▼
          [Phase 3: Apply]
          Process approved items in batches
          ┌─ For each item:
          │  1. Rename folder on disk
          │  2. PUT new path to arr
          │  3. Update status (done / error)
          └─ SSE live output to UI
          Show summary when all batches complete
```

---

## Phase 1 — Scan

### What happens

1. User selects **Radarr** or **Sonarr** from the dashboard.
2. A new `ScanRun` record is created with `status=scanning`.
3. All movies (or series) are fetched via `GET /api/v3/movie` (or `/api/v3/series`).
4. For each item, the expected folder name is computed using the naming template.
5. The current folder name is extracted as the **basename** of the `path` field.
6. If `current != expected`, a `RenameItem` is written with:
   - `status = pending`
   - `current_folder`, `expected_folder` (basenames)
   - `current_path`, `expected_path` (full arr-namespace paths)
7. Items already matching are skipped — no DB entry.
8. The `ScanRun` status is updated to `ready`.
9. UI redirects to `/review`.

### Re-scan behaviour

Re-scanning the same source:
- All `pending`, `approved`, and `skipped` items from the **previous** scan run are deleted.
- `done` items are left intact (their folders already match; they won't appear as mismatches anyway).
- A fresh `ScanRun` is created.
- This means the review page always shows a fresh, accurate list.

### What is NOT scanned

- Items whose `current_folder == expected_folder` → already correct, ignored.
- Items where `path` is empty or null in arr → logged as warning, skipped.

---

## Phase 2 — Review

### The review page shows

| Column | Description |
|---|---|
| Title | Display title (e.g. `Avengers: Endgame`) |
| Current Folder | Folder name as it exists on disk / in arr |
| Expected Folder | What the name should be |
| Status | `pending` → `approved` / `skipped` |
| Action | Approve / Skip toggle |

### User actions

- **Approve** (individual): marks one item `approved`.
- **Skip** (individual): marks one item `skipped` — it will be excluded from Apply.
- **Approve All**: marks all `pending` items `approved` in one click.
- **Batch size**: input at the top; controls how many renames happen per Apply run.

### Skipped items

`skipped` items are permanent for this scan run. They will not appear in the next Apply. They **will** reappear in the next Scan (if the folder name still doesn't match).

---

## Phase 3 — Apply

### What happens

1. The `ScanRun` status changes to `applying`.
2. All `approved` items are loaded and grouped into batches of `batch_size`.
3. Each batch is processed sequentially. Within a batch, items are processed one at a time.

For each `approved` item:

**Step A — Rename on disk**
```
container_path = remap_to_container(item.current_path, root_folder, media_mount)
new_container_path = parent(container_path) / item.expected_folder
os.rename(container_path, new_container_path)
```
- If rename fails: mark `error`, log exception, **skip Step B**, continue to next item.

**Step B — Update arr path**
```
GET /api/v3/movie/{source_id}  →  full object
Modify object['path'] = item.expected_path
PUT /api/v3/movie/{source_id}  →  full object
```
- If PUT fails: mark `error`, log clearly that disk was renamed but arr was NOT updated.
- If PUT succeeds: mark `done`.

**Step C — Log output**
Each step emits a log line to the SSE stream:
```
✔ Dune (2021) {tmdb-438631}  →  renamed OK, arr updated
✘ Avengers - Endgame (2019) {tmdb-299534}  →  disk rename failed: [Errno 13] Permission denied
⚠ Breaking Bad (2008) {tvdb-81189}  →  disk renamed OK, arr update FAILED — manual fix needed
```

4. After all batches complete, the `ScanRun` status is updated to `done` (or `error` if any items errored).
5. UI shows a summary card: **N done / N error**.

### Batching

- Default batch size: 20.
- Operator can change before clicking Apply.
- Batching is sequential — batch 2 starts only after batch 1 finishes.
- The purpose of batching: limits blast radius if something goes wrong partway through; easier to inspect progress via SSE output.

### Live output (SSE)

- The `/apply` page opens an SSE connection to `/api/stream`.
- Each rename step pushes a log line to the in-memory `LogBuffer`.
- The SSE endpoint streams lines from `LogBuffer` to the browser.
- The browser auto-scrolls the log container.
- When the apply run completes, a final `[DONE]` event closes the stream.

---

## Item Status State Machine

```
         ┌──────────────────────────────────────┐
         │                                      │
      [Scan]                                    │
         │                                      │
         ▼                                      │
      pending ──────────────► approved ────────► done
         │                        │
         └──────► skipped         └──────────► error
```

| Status | Meaning | Next states |
|---|---|---|
| `pending` | Detected, awaiting user decision | `approved`, `skipped` |
| `approved` | User approved, will be processed on Apply | `done`, `error` |
| `skipped` | User excluded this item | (terminal for this scan run) |
| `done` | Disk rename + arr update succeeded | (terminal) |
| `error` | One or both steps failed | (terminal; shown in UI) |

---

## Error Recovery

| Error scenario | Disk state | Arr state | Recovery |
|---|---|---|---|
| Disk rename fails | unchanged | unchanged | Fix permissions, re-scan and retry |
| Arr update fails after disk rename | **renamed** | still old path | Manually update path in arr UI, or re-scan (will detect the new folder name as matching, but arr still wrong — needs manual fix) |
| Both succeed | renamed | updated | No action needed |

The error message for "arr update failed after disk rename" explicitly states:
> "Folder was renamed on disk to `{expected_folder}` but Radarr/Sonarr was NOT updated. Update the path manually in the arr UI."
