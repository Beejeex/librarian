"""
routers/tracker_ui.py — HTML page routes for the Tracker.

Serves server-rendered pages using Jinja2 templates + HTMX + Alpine.js.
No business logic lives here — routes fetch data and pass it to templates.

Routes:
  GET  /tracker                              — Tracker dashboard
  GET  /tracker/items                        — Full items table
  GET  /tracker/logs                         — SSE live log viewer
  POST /tracker/items/{id}/approve           — HTMX: approve a queued item
  POST /tracker/items/{id}/skip              — HTMX: skip a queued item
  POST /tracker/items/{id}/reset             — HTMX: reset an item to pending
  POST /tracker/items/approve-all            — approve all queued + poll
  GET  /tracker/dashboard/stats-fragment     — HTMX live stats cards
  GET  /tracker/dashboard/recent-fragment    — HTMX recent activity rows
  GET  /tracker/dashboard/poll-indicator     — HTMX copy-progress indicator
  GET  /tracker/items/rows-fragment          — HTMX live tbody rows
"""

import asyncio
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from app import copy_progress
from app.config import load_config, save_config
from app.database import get_session
from app.models import AppConfig, TrackedItem
from app.scheduler import is_poll_running, reschedule_poll, run_poll

# ---------------------------------------------------------------------------
# Sort helpers
# ---------------------------------------------------------------------------

_SORT_COLS = {
    "title":   TrackedItem.title,
    "type":    TrackedItem.media_type,
    "source":  TrackedItem.source,
    "status":  TrackedItem.status,
    "size":    TrackedItem.file_size_bytes,
    "updated": TrackedItem.updated_at,
}


def _order_by(sort: str, dir: str):
    col = _SORT_COLS.get(sort, TrackedItem.updated_at)
    return col.desc() if dir != "asc" else col.asc()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tracker")
_templates_dir = os.path.join(os.path.dirname(__file__), "../templates")
templates = Jinja2Templates(directory=_templates_dir)


def _filesize_filter(size_bytes) -> str:
    """Jinja2 filter: format a byte count as a human-readable size string."""
    if not size_bytes:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


templates.env.filters["filesize"] = _filesize_filter


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def tracker_dashboard(request: Request):
    """Render the Tracker dashboard with status counts and recent activity."""
    with get_session() as session:
        all_items = session.exec(select(TrackedItem)).all()

    config = load_config()
    counts = {
        "queued": 0, "pending": 0, "copying": 0,
        "copied": 0, "finished": 0, "error": 0,
    }
    for item in all_items:
        counts[item.status] = counts.get(item.status, 0) + 1

    recent = sorted(all_items, key=lambda i: i.updated_at, reverse=True)[:10]

    return templates.TemplateResponse(
        "tracker_dashboard.html",
        {
            "request": request,
            "counts": counts,
            "recent": recent,
            "config": config,
            "poll_running": is_poll_running(),
        },
    )


@router.post("/poll", response_class=HTMLResponse)
async def poll_now():
    """Trigger a manual poll cycle; redirect back to the tracker dashboard."""
    asyncio.create_task(run_poll())
    return RedirectResponse(url="/tracker", status_code=303)


# ---------------------------------------------------------------------------
# Items table
# ---------------------------------------------------------------------------

@router.get("/items", response_class=HTMLResponse)
async def tracker_items(request: Request):
    """Render the full tracked items table."""
    with get_session() as session:
        all_items = session.exec(
            select(TrackedItem).order_by(TrackedItem.updated_at.desc())
        ).all()
    return templates.TemplateResponse(
        "tracker_items.html",
        {"request": request, "items": all_items},
    )


@router.post("/items/{item_id}/approve", response_class=HTMLResponse)
async def approve_item(item_id: int):
    """Approve a single queued item."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if item and item.status == "queued":
            item.status = "pending"
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            session.commit()
            logger.info("Approved tracker item %d (%s).", item_id, item.title)
    return RedirectResponse(url="/tracker/items", status_code=303)


@router.post("/items/{item_id}/skip", response_class=HTMLResponse)
async def skip_item(item_id: int):
    """Permanently skip a queued item (marks it finished immediately)."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if item and item.status == "queued":
            item.status = "finished"
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            session.commit()
            logger.info("Skipped tracker item %d (%s).", item_id, item.title)
    return RedirectResponse(url="/tracker/items", status_code=303)


@router.post("/items/{item_id}/reset", response_class=HTMLResponse)
async def reset_item(item_id: int):
    """Reset a finished, copied, or errored item back to pending."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if item and item.status in ("finished", "copied", "error"):
            item.status = "pending"
            item.error_message = None
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            session.commit()
            logger.info("Reset tracker item %d (%s) to pending.", item_id, item.title)
    return RedirectResponse(url="/tracker/items", status_code=303)


@router.post("/items/approve-all", response_class=HTMLResponse)
async def approve_all_items():
    """Approve all queued items and immediately trigger a poll."""
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
    logger.info("Approved all %d queued tracker items — triggering poll.", count)
    asyncio.create_task(run_poll())
    return RedirectResponse(url="/tracker", status_code=303)


# ---------------------------------------------------------------------------
# Logs page
# ---------------------------------------------------------------------------

@router.get("/logs", response_class=HTMLResponse)
async def tracker_logs(request: Request):
    """Render the Tracker live log viewer (SSE)."""
    return templates.TemplateResponse("tracker_logs.html", {"request": request})


# ---------------------------------------------------------------------------
# HTMX live-refresh fragment routes
# ---------------------------------------------------------------------------

@router.get("/dashboard/stats-fragment", response_class=HTMLResponse)
async def stats_fragment(request: Request):
    """Return just the stat cards HTML for HTMX live-refresh."""
    with get_session() as session:
        all_items = session.exec(select(TrackedItem)).all()
    counts = {
        "queued": 0, "pending": 0, "copying": 0,
        "copied": 0, "finished": 0, "error": 0,
    }
    for item in all_items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return templates.TemplateResponse(
        "_tracker_stats_cards.html",
        {"request": request, "counts": counts},
    )


@router.get("/dashboard/recent-fragment", response_class=HTMLResponse)
async def recent_fragment(request: Request, sort: str = "updated", dir: str = "desc"):
    """Return the recent-activity table content for HTMX live-refresh."""
    with get_session() as session:
        recent = session.exec(
            select(TrackedItem).order_by(_order_by(sort, dir)).limit(20)
        ).all()
    return templates.TemplateResponse(
        "_tracker_recent_fragment.html",
        {"request": request, "recent": recent, "sort": sort, "dir": dir},
    )


@router.get("/dashboard/poll-indicator", response_class=HTMLResponse)
async def poll_indicator(request: Request):
    """Return a copy-progress block when a poll is running, else empty."""
    return templates.TemplateResponse(
        "_tracker_poll_indicator.html",
        {
            "request": request,
            "poll_running": is_poll_running(),
            "copy_jobs": copy_progress.get_all(),
        },
    )


# ---------------------------------------------------------------------------
# Share browser
# ---------------------------------------------------------------------------

@router.get("/share", response_class=HTMLResponse)
async def share_browser(request: Request):
    """Render the share directory browser."""
    config = load_config()
    share_root = Path(config.share_path).resolve()
    folders: list[dict] = []   # [{name, size_bytes, file_count, files: [...]}]
    root_files: list[dict] = []
    total_size = 0
    total_files = 0
    if share_root.exists():
        # Group immediate children: subdirectories get their own bucket, root files go flat
        dir_buckets: dict[str, dict] = {}
        for item in sorted(share_root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if item.is_dir():
                bucket: dict = {"name": item.name, "size_bytes": 0, "file_count": 0, "files": []}
                for f in sorted(item.rglob("*"), key=lambda p: p.name.lower()):
                    if f.is_file():
                        try:
                            sz = f.stat().st_size
                        except OSError:
                            continue
                        rel = f.relative_to(share_root)
                        bucket["size_bytes"] += sz
                        bucket["file_count"] += 1
                        total_size += sz
                        total_files += 1
                        bucket["files"].append({
                            "rel_path": rel.as_posix(),
                            "name": rel.as_posix()[len(item.name) + 1:],  # path under folder
                            "size_bytes": sz,
                        })
                folders.append(bucket)
            elif item.is_file():
                try:
                    sz = item.stat().st_size
                except OSError:
                    continue
                total_size += sz
                total_files += 1
                root_files.append({
                    "rel_path": item.name,
                    "name": item.name,
                    "size_bytes": sz,
                })
    try:
        du = shutil.disk_usage(share_root)
        disk = {"total": du.total, "used": du.used, "free": du.free}
    except Exception:
        disk = None
    return templates.TemplateResponse(
        "tracker_share.html",
        {
            "request": request,
            "folders": folders,
            "root_files": root_files,
            "total_size": total_size,
            "file_count": total_files,
            "disk": disk,
            "share_path": str(share_root),
        },
    )


@router.post("/share/delete-folder", response_class=HTMLResponse)
async def delete_share_folder(folder_name: str = Form(...)):
    """Delete an entire top-level subfolder from the share directory."""
    # Reject any path separators — must be a plain directory name
    if "/" in folder_name or "\\" in folder_name or folder_name in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="Invalid folder name.")
    config = load_config()
    share_root = Path(config.share_path).resolve()
    try:
        target = (share_root / folder_name).resolve()
        target.relative_to(share_root)  # path-traversal guard
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path.")
    # Must be a direct child (depth 1), not share_root itself
    if target.parent != share_root or target == share_root:
        raise HTTPException(status_code=400, detail="Not a direct subfolder.")
    try:
        if target.exists():
            shutil.rmtree(target)
        logger.info("Deleted share folder: %s", target)
        return HTMLResponse("")
    except Exception as exc:
        logger.error("Failed to delete share folder %s: %s", folder_name, exc)
        return HTMLResponse(
            f'<tbody><tr><td colspan="4" style="color:#dc2626;padding:8px 12px">'
            f"Error deleting {folder_name}: {exc}</td></tr></tbody>"
        )


@router.get("/items/rows-fragment", response_class=HTMLResponse)
async def items_rows_fragment(
    request: Request,
    sort: str = "updated",
    dir: str = "desc",
    filter: str = "all",
    search: str = "",
):
    """Return only the <tr> rows for the items table for HTMX tbody refresh."""
    with get_session() as session:
        q = select(TrackedItem)
        if filter != "all":
            q = q.where(TrackedItem.status == filter)
        q = q.order_by(_order_by(sort, dir))
        all_items = session.exec(q).all()
    if search:
        sl = search.lower()
        all_items = [
            i for i in all_items
            if sl in (i.title or "").lower() or sl in (i.series_title or "").lower()
        ]
    return templates.TemplateResponse(
        "_tracker_items_rows.html",
        {"request": request, "items": all_items},
    )
