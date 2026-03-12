# TODO 06 — Apply Engine & Log Buffer

## Goal
Implement `app/renamer.py` and `app/log_buffer.py`. The renamer processes approved `RenameItem` rows in batches: renames folders on disk, updates arr database paths, and streams progress to the UI via `LogBuffer`.

---

## Tasks

### 6.1 — app/log_buffer.py

Thread-safe in-memory log line store. Feeds the SSE stream.

```python
from collections import deque
import threading

class LogBuffer:
    """
    In-memory bounded deque of recent log lines.
    Written to by renamer.py; read by the SSE endpoint.
    """
    def __init__(self, maxlen: int = 500):
        self._buffer: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        """Append a log line (thread-safe)."""

    def tail(self, n: int = 100) -> list[str]:
        """Return the last n lines (thread-safe)."""

    def clear(self) -> None:
        """Clear all lines (thread-safe)."""

    def __len__(self) -> int:
        ...
```

A single global instance is created in `log_buffer.py` and imported wherever needed:
```python
log_buffer = LogBuffer()
```

### 6.2 — app/renamer.py — remap_to_container()

```python
def remap_to_container(
    arr_path: str,
    root_folder: str,
    media_mount: str,
) -> str:
    """
    Translate an arr-namespace path to the container-local mount path.

    Example:
        arr_path    = "/movies/Dune.2021.2160p"
        root_folder = "/movies"
        media_mount = "/media/movies"
        → returns    "/media/movies/Dune.2021.2160p"
    """
    # Strip trailing slashes before replacing to handle inconsistencies
    root = root_folder.rstrip("/")
    path = arr_path.rstrip("/")
    if not path.startswith(root):
        raise ValueError(f"Path '{arr_path}' does not start with root folder '{root_folder}'")
    relative = path[len(root):]
    return media_mount.rstrip("/") + relative
```

### 6.3 — app/renamer.py — run_apply()

```python
async def run_apply(
    scan_run_id: int,
    batch_size: int,
    session: Session,
    config: AppConfig,
) -> None:
    """
    Process all approved RenameItems for a scan run in batches.
    Renames folders on disk, updates arr paths, updates DB status, streams log lines.
    """
```

#### Algorithm

```
1. Load all RenameItems where scan_run_id=scan_run_id AND status="approved"
   ORDER BY id  (deterministic ordering)

2. Update ScanRun.status = "applying"

3. Split into batches of batch_size (plain Python list slicing)

4. For each batch:
   For each item:

   A. Disk rename
      old_local = remap_to_container(item.current_path, root_folder, media_mount)
      new_local = os.path.join(os.path.dirname(old_local), item.expected_folder)

      try:
          await asyncio.to_thread(os.rename, old_local, new_local)
          log_buffer.append(f"✔ [{item.title}] disk rename OK")
      except Exception as e:
          item.status = "error"
          item.error_message = f"Disk rename failed: {e}"
          log_buffer.append(f"✘ [{item.title}] disk rename FAILED: {e}")
          item.updated_at = now()
          session.add(item); session.commit()
          continue   ← skip arr update

   B. Arr path update
      try:
          if item.source == "radarr":
              await radarr_client.update_movie_path(item.source_id, item.expected_path)
          else:
              await sonarr_client.update_series_path(item.source_id, item.expected_path)
          item.status = "done"
          log_buffer.append(f"✔ [{item.title}] arr updated OK")
      except Exception as e:
          item.status = "error"
          item.error_message = (
              f"Disk renamed to '{item.expected_folder}' but arr update FAILED: {e}. "
              "Update the path manually in the arr UI."
          )
          log_buffer.append(f"⚠ [{item.title}] arr update FAILED — disk was renamed. Manual fix needed.")

   C. Save item
      item.updated_at = now()
      session.add(item); session.commit()

5. Update ScanRun counters:
   done_count  = count(status="done")
   error_count = count(status="error")
   status      = "done" if error_count == 0 else "error"
   session.add(scan_run); session.commit()

6. log_buffer.append("[DONE] Apply complete.")
```

#### media_mount mapping
```python
MEDIA_MOUNTS = {"radarr": "/media/movies", "sonarr": "/media/tv"}
```

---

## Tests — tests/test_renamer.py

### Fixtures needed
- `db_session` — in-memory SQLite
- `tmp_media` — `tmp_path / "movies"` with a pre-created folder `Dune.2021.2160p`
- `sample_rename_item` — `RenameItem` with current_folder="Dune.2021.2160p", expected_folder="Dune (2021) {tmdb-438631}"

### remap_to_container tests

| Input | Expected |
|---|---|
| path="/movies/Dune", root="/movies", mount="/media/movies" | "/media/movies/Dune" |
| path="/tv/BB", root="/tv", mount="/media/tv" | "/media/tv/BB" |
| path="/wrong/Dune", root="/movies" | raises ValueError |

### run_apply tests

| Test | Description |
|---|---|
| `test_apply_renames_folder_on_disk` | item approved → folder renamed in tmp_media |
| `test_apply_calls_arr_update` | item approved → radarr_client.update_movie_path called with new path |
| `test_apply_marks_done` | successful rename + arr update → item status="done" |
| `test_apply_disk_fail_marks_error` | os.rename raises → item status="error", arr NOT called |
| `test_apply_arr_fail_marks_error_with_message` | os.rename ok, arr raises → status="error", error_message mentions manual fix |
| `test_apply_continues_after_error` | first item fails → second item still processed |
| `test_apply_updates_scan_run_counters` | after apply, ScanRun.done_count and error_count match |

---

## Acceptance Criteria
- [ ] `LogBuffer.append()` and `tail()` work correctly and are thread-safe
- [ ] `remap_to_container()` translates paths correctly, raises on mismatch
- [ ] `run_apply()` renames folders using `asyncio.to_thread(os.rename, ...)`
- [ ] Disk rename failure: item marked `error`, arr call skipped
- [ ] Arr update failure after disk rename: item marked `error` with explicit manual-fix message
- [ ] A single item error never aborts the batch
- [ ] All tests pass
