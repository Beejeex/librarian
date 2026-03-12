# TODO 07 — API Routes

## Goal
Implement `app/routers/api.py` — all JSON/action endpoints and the SSE stream. No HTML rendering here; this router handles triggers and data mutations.

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/scan` | Trigger scan for a source |
| POST | `/api/items/{id}/approve` | Approve one item |
| POST | `/api/items/{id}/skip` | Skip one item |
| POST | `/api/items/approve-all` | Approve all pending items |
| POST | `/api/apply` | Trigger apply (background task) |
| GET | `/api/stream` | SSE live output stream |
| POST | `/api/logs/clear` | Clear the log buffer |
| GET | `/api/scan-run/latest` | Get latest scan run for a source |

---

## Tasks

### 7.1 — GET /health

```python
@router.get("/health")
async def health():
    return {"status": "ok"}
```

### 7.2 — POST /api/scan

```python
@router.post("/api/scan")
async def trigger_scan(
    source: str,           # query param: "radarr" or "sonarr"
    session: Session = Depends(get_session),
):
    """Trigger a scan for the given source. Redirects to /review on completion."""
```

- Validate `source` is `"radarr"` or `"sonarr"`.
- Call `run_scan(source, session, config)`.
- Return `RedirectResponse("/review")`.

### 7.3 — POST /api/items/{id}/approve

```python
@router.post("/api/items/{item_id}/approve")
async def approve_item(item_id: int, session: Session = Depends(get_session)):
    """Set a RenameItem status to 'approved'."""
```

- Load item, set `status="approved"`, `updated_at=now()`, save.
- Return partial HTML fragment (for HTMX swap) or `{"ok": true}`.

### 7.4 — POST /api/items/{id}/skip

Same as approve but `status="skipped"`.

### 7.5 — POST /api/items/approve-all

```python
@router.post("/api/items/approve-all")
async def approve_all(
    scan_run_id: int,      # query param
    session: Session = Depends(get_session),
):
    """Set all pending items for a scan run to 'approved'."""
```

- Update all `RenameItem` rows where `scan_run_id=scan_run_id AND status="pending"` → `status="approved"`.
- Return redirect to `/review` or JSON count.

### 7.6 — POST /api/apply

```python
@router.post("/api/apply")
async def trigger_apply(
    background_tasks: BackgroundTasks,
    scan_run_id: int,
    batch_size: int = 20,
    session: Session = Depends(get_session),
):
    """Trigger apply as a background task. Redirects to /apply (SSE page)."""
```

- Enqueue `run_apply(scan_run_id, batch_size, session, config)` as a `BackgroundTask`.
- Return `RedirectResponse("/apply")`.

> **Note**: FastAPI's `BackgroundTasks` is fine here since apply is a single user operation. It runs after the response is sent.

### 7.7 — GET /api/stream (SSE)

```python
@router.get("/api/stream")
async def stream_logs(request: Request):
    """SSE endpoint that streams lines from LogBuffer to the browser."""
    async def event_generator():
        last_index = 0
        while True:
            if await request.is_disconnected():
                break
            lines = log_buffer.tail(200)
            new_lines = lines[last_index:]
            for line in new_lines:
                yield {"data": line}
                last_index += 1
            if new_lines and new_lines[-1] == "[DONE] Apply complete.":
                break
            await asyncio.sleep(0.3)

    return EventSourceResponse(event_generator())
```

### 7.8 — POST /api/logs/clear

Calls `log_buffer.clear()`. Returns `{"ok": true}`.

### 7.9 — GET /api/scan-run/latest

Returns the most recent `ScanRun` for a given source as JSON (used to populate dashboard summary).

---

## Tests — tests/test_api.py

| Test | Description |
|---|---|
| `test_health` | GET /health → 200, `{"status": "ok"}` |
| `test_approve_item` | POST /api/items/1/approve → item status becomes "approved" |
| `test_skip_item` | POST /api/items/1/skip → item status becomes "skipped" |
| `test_approve_all` | POST /api/items/approve-all → all pending become approved |
| `test_scan_invalid_source` | POST /api/scan?source=invalid → 422 |
| `test_apply_redirects` | POST /api/apply → 307 redirect to /apply |

---

## Acceptance Criteria
- [ ] `GET /health` returns `{"status": "ok"}` HTTP 200
- [ ] Approve and skip endpoints update item status in DB
- [ ] Approve-all updates all pending items in one call
- [ ] SSE stream yields log lines and closes after `[DONE]`
- [ ] Invalid source param returns 422
- [ ] All API tests pass
