"""
routers/api.py — REST API endpoints for Librarian.

Provides:
  GET  /health                      — liveness probe
  POST /api/scan                    — trigger a scan (Radarr or Sonarr)
  GET  /api/scan-run/latest         — latest ScanRun for a source
  POST /api/items/{id}/approve      — approve a RenameItem
  POST /api/items/{id}/skip         — skip a RenameItem
  POST /api/items/approve-all       — approve all pending items for a scan run
  POST /api/apply                   — start apply in background; streams via SSE
  GET  /api/stream                  — SSE endpoint for live apply output
  POST /api/logs/clear              — clear the in-memory log buffer
  GET  /api/settings                — fetch current AppConfig as JSON
  POST /api/settings                — save AppConfig
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from sqlmodel import Session, select

from app.config import get_config, save_config
from app.database import get_session_dep as get_session
from app.log_buffer import log_buffer
from app.models import AppConfig, RenameItem, ScanRun
from app.renamer import run_apply
from app.scanner import run_scan

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@router.get("/health", tags=["health"])
async def health() -> dict:
    """Return 200 OK — used by Docker HEALTHCHECK."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
class ScanRequest(BaseModel):
    source: str  # "radarr" or "sonarr"


@router.post("/api/scan", tags=["scan"])
async def trigger_scan(
    body: ScanRequest,
    session: Session = Depends(get_session),
) -> dict:
    """
    Kick off a scan for the given source.

    Fetches all items from the arr API, computes mismatches, and writes
    pending RenameItems to the DB.  Returns the new ScanRun id.
    """
    if body.source not in ("radarr", "sonarr"):
        raise HTTPException(status_code=400, detail="source must be 'radarr' or 'sonarr'")

    config = get_config(session)
    try:
        scan_run = await run_scan(body.source, session, config)
    except Exception as exc:
        logger.error("Scan failed for source=%s: %s", body.source, exc)
        # Return error payload instead of crashing — UI displays it as an alert
        return {"error": str(exc), "scan_run_id": None, "total_items": 0}
    return {"scan_run_id": scan_run.id, "total_items": scan_run.total_items}


# ---------------------------------------------------------------------------
# Scan run status
# ---------------------------------------------------------------------------
@router.get("/api/scan-run/latest", tags=["scan"])
async def latest_scan_run(
    source: str,
    session: Session = Depends(get_session),
) -> dict:
    """Return the most recent ScanRun for the given source."""
    stmt = (
        select(ScanRun)
        .where(ScanRun.source == source)
        .order_by(ScanRun.id.desc())  # type: ignore[arg-type]
        .limit(1)
    )
    run = session.exec(stmt).first()
    if not run:
        return {}
    return {
        "id": run.id,
        "source": run.source,
        "status": run.status,
        "total_items": run.total_items,
        "done_count": run.done_count,
        "error_count": run.error_count,
    }


# ---------------------------------------------------------------------------
# Item approval / skip
# ---------------------------------------------------------------------------
def _get_item_or_404(item_id: int, session: Session) -> RenameItem:
    item = session.get(RenameItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.post("/api/items/{item_id}/approve", tags=["items"])
async def approve_item(
    item_id: int,
    session: Session = Depends(get_session),
) -> dict:
    """Mark a RenameItem as approved."""
    item = _get_item_or_404(item_id, session)
    item.status = "approved"
    item.updated_at = datetime.now(UTC)
    session.add(item)
    session.commit()
    return {"id": item.id, "status": item.status}


@router.post("/api/items/{item_id}/skip", tags=["items"])
async def skip_item(
    item_id: int,
    session: Session = Depends(get_session),
) -> dict:
    """Mark a RenameItem as skipped (will not be renamed)."""
    item = _get_item_or_404(item_id, session)
    item.status = "skipped"
    item.updated_at = datetime.now(UTC)
    session.add(item)
    session.commit()
    return {"id": item.id, "status": item.status}


class ApproveAllRequest(BaseModel):
    scan_run_id: int


@router.post("/api/items/approve-all", tags=["items"])
async def approve_all_items(
    body: ApproveAllRequest,
    session: Session = Depends(get_session),
) -> dict:
    """Approve all pending RenameItems for a given ScanRun."""
    stmt = select(RenameItem).where(
        RenameItem.scan_run_id == body.scan_run_id,
        RenameItem.status == "pending",
    )
    items = session.exec(stmt).all()
    for item in items:
        item.status = "approved"
        item.updated_at = datetime.now(UTC)
        session.add(item)
    session.commit()
    return {"approved": len(items)}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
class ApplyRequest(BaseModel):
    scan_run_id: int
    batch_size: int = 20


@router.post("/api/apply", tags=["apply"])
async def start_apply(
    body: ApplyRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict:
    """
    Start the apply process in a background task.

    Returns immediately; the UI connects to /api/stream for live output.
    """
    config = get_config(session)

    async def _run():
        # Use a fresh session inside background task — sessions are not thread-safe
        from app.database import get_session as _gs
        with _gs() as bg_session:
            await run_apply(body.scan_run_id, body.batch_size, bg_session, config)

    background_tasks.add_task(_run)
    return {"started": True}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestConnectionRequest(BaseModel):
    source: str  # "radarr" or "sonarr"
    url: str
    api_key: str


@router.post("/api/test-connection", tags=["settings"])
async def test_connection(body: TestConnectionRequest) -> dict:
    """
    Test connectivity to Radarr or Sonarr and return the folder naming format.

    Returns: { success: bool, folder_format: str, error?: str }
    """
    if body.source not in ("radarr", "sonarr"):
        raise HTTPException(status_code=400, detail="source must be 'radarr' or 'sonarr'")

    from app.radarr import RadarrClient
    from app.sonarr import SonarrClient

    try:
        if body.source == "radarr":
            client = RadarrClient(body.url, body.api_key)
        else:
            client = SonarrClient(body.url, body.api_key)
        folder_format = await client.fetch_folder_format()
        return {"success": True, "folder_format": folder_format}
    except Exception as exc:
        logger.warning("Test connection failed for source=%s: %s", body.source, exc)
        return {"success": False, "folder_format": "", "error": str(exc)}


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------
@router.get("/api/stream", tags=["apply"])
async def stream_logs() -> EventSourceResponse:
    """
    Server-Sent Events endpoint.  Tails log_buffer and pushes new lines.
    The client disconnects when done; we stop on '[DONE]' sentinel.
    """
    async def _generator() -> AsyncIterator[dict]:
        sent_index = 0
        while True:
            lines = log_buffer.tail(500)
            new_lines = lines[sent_index:]
            for line in new_lines:
                yield {"data": line}
                sent_index += 1
                if line.startswith("[DONE]"):
                    return
            await asyncio.sleep(0.3)

    return EventSourceResponse(_generator())


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
@router.post("/api/logs/clear", tags=["logs"])
async def clear_logs() -> dict:
    """Clear the in-memory log buffer."""
    log_buffer.clear()
    return {"cleared": True}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class SettingsPayload(BaseModel):
    radarr_url: str = ""
    radarr_api_key: str = ""
    radarr_root_folder: str = "/movies"
    radarr_folder_format: str = "{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}"
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    sonarr_root_folder: str = "/tv"
    sonarr_folder_format: str = "{Series TitleYear} {tvdb-{TvdbId}}"
    batch_size: int = 20


@router.get("/api/settings", tags=["settings"])
async def get_settings(session: Session = Depends(get_session)) -> dict:
    """Return current AppConfig (API keys are included — settings page only)."""
    config = get_config(session)
    return {
        "radarr_url": config.radarr_url,
        "radarr_api_key": config.radarr_api_key,
        "radarr_root_folder": config.radarr_root_folder,
        "radarr_folder_format": config.radarr_folder_format,
        "sonarr_url": config.sonarr_url,
        "sonarr_api_key": config.sonarr_api_key,
        "sonarr_root_folder": config.sonarr_root_folder,
        "sonarr_folder_format": config.sonarr_folder_format,
        "batch_size": config.batch_size,
    }


@router.post("/api/settings", tags=["settings"])
async def update_settings(
    body: SettingsPayload,
    session: Session = Depends(get_session),
) -> dict:
    """Persist AppConfig changes."""
    save_config(session, body.model_dump())
    return {"saved": True}
