# TODO 05 — Scan Engine

## Goal
Implement `app/scanner.py` — the logic that fetches all items from arr, computes expected folder names, compares with current names, and writes `RenameItem` rows to the DB for anything that doesn't match.

---

## Tasks

### 5.1 — app/scanner.py — run_scan()

```python
async def run_scan(
    source: str,           # "radarr" or "sonarr"
    session: Session,
    config: AppConfig,
) -> ScanRun:
    """
    Fetch all items from arr, compute expected folder names, record mismatches.
    Returns the completed ScanRun record.
    """
```

#### Algorithm

```
1. Create ScanRun(source=source, status="scanning") → save → get scan_run.id

2. Clear existing non-done items for this source:
   DELETE FROM renameitem
   WHERE source = source AND status NOT IN ('done')

3. Fetch items:
   if source == "radarr":
       items = await RadarrClient.fetch_movies()
   else:
       items = await SonarrClient.fetch_series()

4. For each item:
   a. Compute expected = movie_folder_name(item) or series_folder_name(item)
   b. current_path = item["path"]  (may or may not have trailing slash — normalise)
   c. current_folder = os.path.basename(current_path.rstrip("/"))
   d. if current_folder == expected:
          continue  (already correct, skip)
   e. Compute expected_path:
          arr_root = config.radarr_root_folder or sonarr_root_folder
          expected_path = os.path.join(arr_root, expected)  (arr-namespace path)
   f. write RenameItem(
          scan_run_id=scan_run.id,
          source=source,
          source_id=item["id"],
          title=item["title"],
          current_folder=current_folder,
          expected_folder=expected,
          current_path=current_path,
          expected_path=expected_path,
          status="pending",
      )

5. Update ScanRun:
   scan_run.total_items = count of written RenameItems
   scan_run.status = "ready"
   save

6. Return scan_run
```

#### Edge cases to handle
- `item["path"]` is empty string or null → log warning, skip item.
- `item["year"]` is 0 or null → still compute name (year shows as 0; arr data issue, not Librarian's problem).
- `item["tmdbId"]` or `item["tvdbId"]` is 0 or null → log warning, skip item (can't build valid folder name).

### 5.2 — Re-scan logic

When a re-scan is triggered:
- Delete all `RenameItem` rows for this source where `status IN ('pending', 'approved', 'skipped')`.
- Leave `done` and `error` items (they belong to previous scan runs, or `done` items are already correct on disk).
- Create a new `ScanRun` — do not reuse the old one.

---

## Tests — tests/test_scanner.py

### Fixtures needed (from conftest.py)
- `db_session` — in-memory SQLite
- `mock_radarr_movies` — list of 3 sample movie dicts (mix of matching and mismatching)
- `sample_config` — `AppConfig` with radarr_url, radarr_root_folder set

### Test cases

| Test | Description |
|---|---|
| `test_scan_creates_scan_run` | After scan, a ScanRun row exists with status=ready |
| `test_scan_writes_mismatches` | 2 out of 3 movies have wrong names → 2 RenameItems written |
| `test_scan_skips_matching` | Movie whose current folder already matches expected → no RenameItem |
| `test_scan_skips_missing_ids` | Movie with tmdbId=0 → no RenameItem, warning logged |
| `test_scan_skips_empty_path` | Movie with path="" → no RenameItem, warning logged |
| `test_rescan_clears_pending` | Run two scans → second scan deletes pending items from first |
| `test_rescan_keeps_done` | First scan has a done item → re-scan does not delete it |

---

## Acceptance Criteria
- [ ] `run_scan("radarr", ...)` creates a `ScanRun` and writes `RenameItem` rows for mismatches
- [ ] Items already matching are not written
- [ ] Re-scan clears pending/approved/skipped items but not done/error
- [ ] Edge cases (empty path, missing IDs) are handled gracefully with logging
- [ ] All tests pass
