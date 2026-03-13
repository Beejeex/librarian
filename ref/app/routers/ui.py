"""
UI routes for MadTracked.

Serves server-rendered HTML pages using Jinja2 templates + HTMX + Alpine.js.
No business logic lives here — routes only fetch data and pass it to templates.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Annotated, List

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from app.config import load_config, save_config
from app.database import get_session
from app.models import AppConfig, TrackedItem
from app.scheduler import is_poll_running, reschedule_poll, run_poll
from app import copy_progress
from app.version import VERSION

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "../templates"))


def _filesize_filter(size_bytes) -> str:
    """Jinja2 filter: format a byte count as a human-readable size string."""
    if not size_bytes:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


templates.env.filters["filesize"] = _filesize_filter
templates.env.globals["VERSION"] = VERSION


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the dashboard with item status counts and recent activity."""
    with get_session() as session:
        all_items = session.exec(select(TrackedItem)).all()

    config = load_config()

    # Build status counts for the summary cards
    counts = {"queued": 0, "pending": 0, "copying": 0, "copied": 0, "finished": 0, "error": 0}
    for item in all_items:
        counts[item.status] = counts.get(item.status, 0) + 1

    # Show the 10 most recently updated items as recent activity
    recent = sorted(all_items, key=lambda i: i.updated_at, reverse=True)[:10]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "counts": counts,
            "recent": recent,
            "config": config,
        },
    )


@router.get("/items", response_class=HTMLResponse)
async def items_page(request: Request):
    """Render the full tracked items table."""
    with get_session() as session:
        all_items = session.exec(select(TrackedItem).order_by(TrackedItem.updated_at.desc())).all()

    return templates.TemplateResponse(
        "items.html",
        {"request": request, "items": all_items},
    )


@router.post("/items/{item_id}/reset", response_class=HTMLResponse)
async def reset_item(item_id: int):
    """Reset a finished, copied, or errored item back to pending for re-copy on next poll."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if item and item.status in ("finished", "copied", "error"):
            item.status = "pending"
            item.error_message = None
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            session.commit()
            logger.info("Reset item %d (%s) to pending.", item_id, item.title)
    return RedirectResponse(url="/items", status_code=303)


@router.post("/items/{item_id}/approve", response_class=HTMLResponse)
async def approve_item(item_id: int):
    """Approve a single queued item — moves it to pending for copying on the next poll."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if item and item.status == "queued":
            item.status = "pending"
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            session.commit()
            logger.info("Approved item %d (%s).", item_id, item.title)
    return RedirectResponse(url="/items", status_code=303)


@router.post("/items/approve-all", response_class=HTMLResponse)
async def approve_all_items():
    """Approve all queued items and immediately trigger a poll to start copying."""
    import asyncio
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
    logger.info("Approved all %d queued items — triggering poll.", count)
    # Fire-and-forget poll so copying starts immediately
    asyncio.create_task(run_poll())
    return RedirectResponse(url="/", status_code=303)


@router.post("/items/{item_id}/skip", response_class=HTMLResponse)
async def skip_item(item_id: int):
    """Permanently skip a queued item without copying it."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if item and item.status == "queued":
            item.status = "finished"
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            session.commit()
            logger.info("Skipped item %d (%s).", item_id, item.title)
    return RedirectResponse(url="/items", status_code=303)


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """Render the configuration form pre-filled with current settings."""
    config = load_config()
    saved = request.query_params.get("saved") == "1"
    return templates.TemplateResponse(
        "config.html",
        {"request": request, "config": config, "saved": saved},
    )


@router.post("/config", response_class=HTMLResponse)
async def save_config_form(
    request: Request,
    radarr_url: Annotated[str, Form()] = "",
    radarr_api_key: Annotated[str, Form()] = "",
    radarr_tags: Annotated[List[str], Form()] = [],
    radarr_root_folder: Annotated[str, Form()] = "/movies",
    sonarr_url: Annotated[str, Form()] = "",
    sonarr_api_key: Annotated[str, Form()] = "",
    sonarr_tags: Annotated[List[str], Form()] = [],
    sonarr_root_folder: Annotated[str, Form()] = "/tv",
    poll_interval_minutes: Annotated[int, Form()] = 15,
    share_path: Annotated[str, Form()] = "/share",
    copy_mode: Annotated[str, Form()] = "copy",
    require_approval: Annotated[bool, Form()] = False,
    max_concurrent_copies: Annotated[int, Form()] = 2,
    max_share_size_gb: Annotated[float, Form()] = 0.0,
    max_share_files: Annotated[int, Form()] = 0,
    ntfy_url: Annotated[str, Form()] = "https://ntfy.sh",
    ntfy_topic: Annotated[str, Form()] = "",
    ntfy_token: Annotated[str, Form()] = "",
    ntfy_on_copied: Annotated[bool, Form()] = False,
    ntfy_on_error: Annotated[bool, Form()] = False,
    ntfy_on_finished: Annotated[bool, Form()] = False,
    ntfy_on_first_run: Annotated[bool, Form()] = False,
):
    """Save the submitted configuration form and redirect back to the config page."""
    # Load existing config to preserve first_run_complete (never set via form)
    existing = load_config()
    updated = AppConfig(
        radarr_url=radarr_url,
        radarr_api_key=radarr_api_key,
        radarr_tags=",".join(radarr_tags),
        radarr_root_folder=radarr_root_folder,
        sonarr_url=sonarr_url,
        sonarr_api_key=sonarr_api_key,
        sonarr_tags=",".join(sonarr_tags),
        sonarr_root_folder=sonarr_root_folder,
        poll_interval_minutes=poll_interval_minutes,
        share_path=share_path,
        copy_mode=copy_mode,
        radarr_first_run_complete=existing.radarr_first_run_complete,  # Preserve — never reset via form
        sonarr_first_run_complete=existing.sonarr_first_run_complete,
        require_approval=require_approval,
        max_concurrent_copies=max_concurrent_copies,
        max_share_size_gb=max_share_size_gb,
        max_share_files=max_share_files,
        ntfy_url=ntfy_url,
        ntfy_topic=ntfy_topic,
        ntfy_token=ntfy_token,
        ntfy_on_copied=ntfy_on_copied,
        ntfy_on_error=ntfy_on_error,
        ntfy_on_finished=ntfy_on_finished,
        ntfy_on_first_run=ntfy_on_first_run,
    )
    save_config(updated)
    # Hot-reload the poll interval if it changed
    if updated.poll_interval_minutes != existing.poll_interval_minutes:
        reschedule_poll(updated.poll_interval_minutes)
    return RedirectResponse(url="/config?saved=1", status_code=303)


@router.post("/config/reset-radarr-first-run", response_class=HTMLResponse)
async def reset_radarr_first_run():
    """Reset radarr_first_run_complete so the next poll re-indexes Radarr items as backlog."""
    config = load_config()
    config.radarr_first_run_complete = False
    save_config(config)
    logger.warning("Radarr first-run index reset — next poll will re-index all Radarr items.")
    return RedirectResponse(url="/config?saved=1", status_code=303)


@router.post("/config/reset-sonarr-first-run", response_class=HTMLResponse)
async def reset_sonarr_first_run():
    """Reset sonarr_first_run_complete so the next poll re-indexes Sonarr items as backlog."""
    config = load_config()
    config.sonarr_first_run_complete = False
    save_config(config)
    logger.warning("Sonarr first-run index reset — next poll will re-index all Sonarr items.")
    return RedirectResponse(url="/config?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Live-update fragment routes (polled by HTMX for real-time UI refresh)
# ---------------------------------------------------------------------------

@router.get("/dashboard/poll-indicator", response_class=HTMLResponse)
async def dashboard_poll_indicator(request: Request):
    """Return a copy-progress block when a poll is running, else empty."""
    return templates.TemplateResponse(
        "_poll_indicator.html",
        {
            "request": request,
            "poll_running": is_poll_running(),
            "copy_jobs": copy_progress.get_all(),
        },
    )


@router.get("/dashboard/stats-fragment", response_class=HTMLResponse)
async def dashboard_stats_fragment(request: Request):
    """Return just the five stat cards HTML, for HTMX live-refresh."""
    with get_session() as session:
        all_items = session.exec(select(TrackedItem)).all()
    counts = {"queued": 0, "pending": 0, "copying": 0, "copied": 0, "finished": 0, "error": 0}
    for item in all_items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return templates.TemplateResponse(
        "_stats_cards.html",
        {"request": request, "counts": counts},
    )


@router.get("/dashboard/recent-fragment", response_class=HTMLResponse)
async def dashboard_recent_fragment(request: Request):
    """Return the recent-activity table content, for HTMX live-refresh."""
    with get_session() as session:
        all_items = session.exec(select(TrackedItem)).all()
    recent = sorted(all_items, key=lambda i: i.updated_at, reverse=True)[:10]
    return templates.TemplateResponse(
        "_recent_fragment.html",
        {"request": request, "recent": recent},
    )


@router.get("/items/rows-fragment", response_class=HTMLResponse)
async def items_rows_fragment(request: Request):
    """Return only the <tr> rows for the items table, for HTMX live-refresh of the tbody."""
    with get_session() as session:
        all_items = session.exec(select(TrackedItem).order_by(TrackedItem.updated_at.desc())).all()
    return templates.TemplateResponse(
        "_items_rows.html",
        {"request": request, "items": all_items},
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """Render the logs page (placeholder — log tailing via SSE not yet implemented)."""
    return templates.TemplateResponse("logs.html", {"request": request})
