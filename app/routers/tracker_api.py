"""
routers/tracker_api.py — REST API endpoints for the Tracker.

Provides:
  GET  /api/tracker/items               — list TrackedItems
  GET  /api/tracker/items/{id}          — single TrackedItem
  POST /api/tracker/items/{id}/approve  — approve a queued item
  POST /api/tracker/items/{id}/skip     — skip a queued item
  POST /api/tracker/items/{id}/reset    — reset finished/copied/error to pending
  POST /api/tracker/items/approve-all   — approve all queued + trigger poll
  POST /api/tracker/poll                — trigger manual poll now
  GET  /api/tracker/logs/recent         — last N log lines (plain text)
  POST /api/tracker/logs/clear          — clear the log buffer
  GET  /api/tracker/logs/stream         — SSE live log stream
  GET  /api/tracker/share/stats         — JSON quota + FS stats
  GET  /api/tracker/share/stats-html    — HTML progress-bar fragment for HTMX
  GET  /api/tracker/radarr/tags         — HTML <select> fragment of Radarr tags
  GET  /api/tracker/sonarr/tags         — HTML <select> fragment of Sonarr tags
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlmodel import select

from app.config import load_config
from app.copier import get_quota_usage, get_share_stats
from app.database import get_session
from app.log_buffer import (
    clear_logs,
    get_log_queue,
    get_recent_logs,
    unsubscribe_log_queue,
)
from app.models import TrackedItem
from app.scheduler import run_poll

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tracker")


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

@router.get("/items", response_model=list[TrackedItem])
def list_items():
    """Return all tracked items ordered by most recently updated."""
    with get_session() as session:
        items = session.exec(
            select(TrackedItem).order_by(TrackedItem.updated_at.desc())
        ).all()
    return items


@router.get("/items/{item_id}", response_model=TrackedItem)
def get_item(item_id: int):
    """Return a single tracked item by ID."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")
    return item


@router.post("/items/{item_id}/approve", response_model=TrackedItem)
def approve_item(item_id: int):
    """Approve a queued item — moves it to pending for copying on the next poll."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found.")
        if item.status != "queued":
            raise HTTPException(status_code=400, detail="Only queued items can be approved.")
        item.status = "pending"
        item.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.commit()
        session.refresh(item)
        logger.info("Approved tracker item %d (%s).", item_id, item.title)
        return item


@router.post("/items/{item_id}/skip", response_model=TrackedItem)
def skip_item(item_id: int):
    """Permanently skip a queued item without copying it."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found.")
        if item.status != "queued":
            raise HTTPException(status_code=400, detail="Only queued items can be skipped.")
        item.status = "finished"
        item.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.commit()
        session.refresh(item)
        logger.info("Skipped tracker item %d (%s).", item_id, item.title)
        return item


@router.post("/items/{item_id}/reset", response_model=TrackedItem)
def reset_item(item_id: int):
    """Reset a finished, copied, or errored item back to pending for re-copy on next poll."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found.")
        if item.status not in ("finished", "copied", "error"):
            raise HTTPException(
                status_code=400,
                detail="Only finished, copied, or errored items can be reset.",
            )
        item.status = "pending"
        item.error_message = None
        item.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.commit()
        session.refresh(item)
        logger.info("Reset tracker item %d (%s) to pending.", item_id, item.title)
        return item


@router.post("/items/approve-all")
async def approve_all_items():
    """Approve all queued items and immediately trigger a poll to start copying."""
    count = 0
    with get_session() as session:
        items = session.exec(
            select(TrackedItem).where(TrackedItem.status == "queued")
        ).all()
        for item in items:
            item.status = "pending"
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            count += 1
        session.commit()
    logger.info("Approved %d queued tracker items — triggering poll.", count)
    asyncio.create_task(run_poll())
    return {"status": "ok", "approved": count, "poll": "triggered"}


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------

@router.post("/poll")
async def trigger_poll():
    """Manually trigger a tracker poll cycle immediately."""
    logger.info("Manual poll triggered via Tracker API.")
    asyncio.create_task(run_poll())
    return {"status": "poll triggered"}


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@router.get("/logs/recent", response_class=PlainTextResponse)
def recent_logs(n: int = 100):
    """Return the last n log lines as plain text for the UI log viewer."""
    return "\n".join(get_recent_logs(n))


@router.post("/logs/clear", response_class=PlainTextResponse)
def clear_log_buffer():
    """Clear the in-memory log buffer and return empty content so the UI empties immediately."""
    clear_logs()
    return ""


@router.get("/logs/stream")
async def logs_stream():
    """
    Stream live log lines to the browser via Server-Sent Events.

    On connect, sends the last 100 buffered lines as backlog so the client
    has immediate context. Then streams new lines as they arrive.
    A 15-second keepalive comment is sent when there is no log activity.
    """
    from sse_starlette.sse import EventSourceResponse

    queue = get_log_queue()

    async def event_generator():
        try:
            # Backlog snapshot so the client sees recent history on connect
            for line in get_recent_logs(100):
                yield {"data": line}
            # Stream new log lines as they arrive
            while True:
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=15)
                    yield {"data": line}
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        finally:
            unsubscribe_log_queue(queue)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Share stats
# ---------------------------------------------------------------------------

@router.get("/share/stats")
async def share_stats():
    """Return quota usage (DB-based) and filesystem share stats."""
    config = load_config()
    fs_stats = await asyncio.to_thread(get_share_stats, config.share_path)
    with get_session() as session:
        backlog = get_quota_usage(session, is_backlog=True)
        total = get_quota_usage(session, is_backlog=None)
    return {
        "filesystem": fs_stats,
        "backlog": {
            **backlog,
            "limit_gb": round(config.max_share_size_gb * 0.6, 3),
            "limit_files": int(config.max_share_files * 0.6),
        },
        "total": total,
    }


@router.get("/share/stats-html", response_class=HTMLResponse)
async def share_stats_html():
    """Return an HTML progress-bar fragment for the dashboard share-usage widget (HTMX swap)."""
    config = load_config()
    fs_stats = await asyncio.to_thread(get_share_stats, config.share_path)
    with get_session() as session:
        backlog = get_quota_usage(session, is_backlog=True)
        total = get_quota_usage(session, is_backlog=None)

    def pct(used_gb: float, limit_gb: float) -> int:
        if limit_gb <= 0:
            return 0
        return min(100, int(used_gb / limit_gb * 100))

    b_limit = round(config.max_share_size_gb * 0.6, 2)
    b_pct = pct(backlog["size_gb"], b_limit)
    b_label = (
        f"{backlog['size_gb']} / {b_limit} GB"
        if b_limit > 0
        else f"{backlog['size_gb']} GB (unlimited)"
    )

    t_limit = round(config.max_share_size_gb, 2)
    t_pct = pct(total["size_gb"], t_limit)
    t_label = (
        f"{total['size_gb']} / {t_limit} GB"
        if t_limit > 0
        else f"{total['size_gb']} GB (unlimited)"
    )

    return f"""
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
        <div>
            <div style="font-size:.8rem;color:#64748b;margin-bottom:.25rem">Backlog &mdash; {b_label}</div>
            <div style="background:#e2e8f0;border-radius:999px;height:8px;overflow:hidden">
                <div style="background:#f59e0b;height:100%;width:{b_pct}%;transition:width .3s"></div>
            </div>
            <div style="font-size:.75rem;color:#94a3b8;margin-top:.2rem">{backlog['file_count']} files</div>
        </div>
        <div>
            <div style="font-size:.8rem;color:#64748b;margin-bottom:.25rem">Total &mdash; {t_label}</div>
            <div style="background:#e2e8f0;border-radius:999px;height:8px;overflow:hidden">
                <div style="background:#0284c7;height:100%;width:{t_pct}%;transition:width .3s"></div>
            </div>
            <div style="font-size:.75rem;color:#94a3b8;margin-top:.2rem">{total['file_count']} files</div>
        </div>
    </div>
    <p style="color:#94a3b8;font-size:.8rem;margin-top:.5rem">
        {fs_stats['file_count']} files &middot; {fs_stats['size_gb']} GB on share
    </p>
    """


# ---------------------------------------------------------------------------
# Tag list helpers (HTMX <select> fragments)
# ---------------------------------------------------------------------------

def _build_tag_select(
    select_id: str,
    select_name: str,
    tags: list[dict],
    selected: str,
    error: str | None,
) -> str:
    """Build an HTML <select multiple> fragment from a list of tag dicts for HTMX swap."""
    selected_set = {s.strip() for s in selected.split(",") if s.strip()}
    if error:
        options = f'<option value="">⚠ {error}</option>'
    elif not tags:
        options = '<option value="">— no tags found —</option>'
    else:
        options = ""
        for tag in sorted(tags, key=lambda t: t["label"]):
            sel = " selected" if tag["label"] in selected_set else ""
            label = tag["label"]
            options += f'<option value="{label}"{sel}>{label}</option>'
    return (
        f'<select id="{select_id}" name="{select_name}" multiple'
        f' style="flex:1;min-height:80px">{options}</select>'
    )


@router.get("/radarr/tags", response_class=HTMLResponse)
async def radarr_tags(
    radarr_url: str = "",
    radarr_api_key: str = "",
    selected: str = "",
):
    """Proxy Radarr's tag list and return an HTML <select multiple> fragment for HTMX."""
    tags, error = [], None
    if radarr_url and radarr_api_key:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{radarr_url.rstrip('/')}/api/v3/tag",
                    headers={"X-Api-Key": radarr_api_key},
                )
                resp.raise_for_status()
                tags = resp.json()
        except httpx.TimeoutException:
            error = "connection timed out"
        except httpx.HTTPStatusError as exc:
            error = f"HTTP {exc.response.status_code}"
        except Exception as exc:
            error = str(exc)
    else:
        error = "enter URL and API key first"
    return _build_tag_select("radarr_tags_tracker", "radarr_tags", tags, selected, error)


@router.get("/sonarr/tags", response_class=HTMLResponse)
async def sonarr_tags(
    sonarr_url: str = "",
    sonarr_api_key: str = "",
    selected: str = "",
):
    """Proxy Sonarr's tag list and return an HTML <select multiple> fragment for HTMX."""
    tags, error = [], None
    if sonarr_url and sonarr_api_key:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{sonarr_url.rstrip('/')}/api/v3/tag",
                    headers={"X-Api-Key": sonarr_api_key},
                )
                resp.raise_for_status()
                tags = resp.json()
        except httpx.TimeoutException:
            error = "connection timed out"
        except httpx.HTTPStatusError as exc:
            error = f"HTTP {exc.response.status_code}"
        except Exception as exc:
            error = str(exc)
    else:
        error = "enter URL and API key first"
    return _build_tag_select("sonarr_tags_tracker", "sonarr_tags", tags, selected, error)
