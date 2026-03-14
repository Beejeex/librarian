"""
routers/ui.py — HTML page routes (Jinja2 server-rendered).

Routes:
  GET  /            Dashboard: source picker + last scan summary
  GET  /review      Mismatch table: approve/skip items, Apply button
  GET  /apply       SSE live-output page (shown while apply is running)
  GET  /settings    Config form (API URLs, keys, root folders, batch size)
  POST /settings    Save AppConfig, redirect back to settings
  GET  /logs        Recent log output page with Clear button
"""

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.config import get_config, save_config
from app.database import get_session_dep as get_session
from app.log_buffer import log_buffer
from app.models import RenameItem, ScanRun, TrackedItem

logger = logging.getLogger(__name__)
router = APIRouter()

# Templates instance imported from main after app is created;
# we wire it up via dependency so tests can override it.
_templates: Jinja2Templates | None = None


def get_templates() -> Jinja2Templates:
    """Return the shared Jinja2Templates instance (set by main.py on startup)."""
    if _templates is None:
        from app.main import templates  # lazy import to avoid circular dependency
        return templates
    return _templates


# ---------------------------------------------------------------------------
# Dashboard  /
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse, tags=["ui"])
async def dashboard(
    request: Request,
    session: Session = Depends(get_session),
):
    """Render the dashboard: source picker + last scan summary cards."""
    config = get_config(session)

    # Fetch latest scan run for each source
    def _latest(source: str) -> ScanRun | None:
        return session.exec(
            select(ScanRun)
            .where(ScanRun.source == source)
            .order_by(ScanRun.id.desc())  # type: ignore[arg-type]
            .limit(1)
        ).first()

    radarr_run = _latest("radarr")
    sonarr_run = _latest("sonarr")

    # Tracker summary counts for the dashboard widget
    all_tracked = session.exec(select(TrackedItem)).all()
    tracker_counts = {"queued": 0, "pending": 0, "copying": 0, "copied": 0, "finished": 0, "error": 0}
    for item in all_tracked:
        tracker_counts[item.status] = tracker_counts.get(item.status, 0) + 1

    templates = get_templates()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "radarr_run": radarr_run,
            "sonarr_run": sonarr_run,
            "config": config,
            "tracker_counts": tracker_counts,
        },
    )


# ---------------------------------------------------------------------------
# Review  /review
# ---------------------------------------------------------------------------
@router.get("/review", response_class=HTMLResponse, tags=["ui"])
async def review(
    request: Request,
    source: str = "radarr",
    session: Session = Depends(get_session),
):
    """
    Render the review page: tabs for Radarr and Sonarr mismatches side by side.
    The `source` query param pre-selects the active tab.
    """
    config = get_config(session)

    def _scan_data(src: str) -> tuple:
        run = session.exec(
            select(ScanRun)
            .where(ScanRun.source == src)
            .order_by(ScanRun.id.desc())  # type: ignore[arg-type]
            .limit(1)
        ).first()
        items: list[RenameItem] = []
        if run:
            items = list(
                session.exec(
                    select(RenameItem)
                    .where(
                        RenameItem.scan_run_id == run.id,
                        RenameItem.status.in_(("pending", "approved", "skipped")),  # type: ignore[attr-defined]
                    )
                    .order_by(RenameItem.id)  # type: ignore[arg-type]
                ).all()
            )
        return run, items

    radarr_run, radarr_items = _scan_data("radarr")
    sonarr_run, sonarr_items = _scan_data("sonarr")

    radarr_configured = bool(config.radarr_url and config.radarr_api_key)
    sonarr_configured = bool(config.sonarr_url and config.sonarr_api_key)

    # Pre-select a valid tab: honour the query param, otherwise pick the first configured source
    if source == "radarr" and radarr_configured:
        active_source = "radarr"
    elif source == "sonarr" and sonarr_configured:
        active_source = "sonarr"
    elif radarr_configured:
        active_source = "radarr"
    elif sonarr_configured:
        active_source = "sonarr"
    else:
        active_source = "radarr"

    templates = get_templates()
    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "active_source": active_source,
            "radarr_run": radarr_run,
            "radarr_items": radarr_items,
            "sonarr_run": sonarr_run,
            "sonarr_items": sonarr_items,
            "batch_size": config.batch_size,
            "radarr_configured": radarr_configured,
            "sonarr_configured": sonarr_configured,
        },
    )


# ---------------------------------------------------------------------------
# Apply log  /apply
# ---------------------------------------------------------------------------
@router.get("/apply", response_class=HTMLResponse, tags=["ui"])
async def apply_page(
    request: Request,
    scan_run_id: int = 0,
    batch_size: int = 20,
):
    """
    Render the apply page.

    The page connects to /api/stream via SSE and displays live log output.
    scan_run_id and batch_size are passed as query params and forwarded to
    the SSE-triggered apply via JavaScript.
    """
    templates = get_templates()
    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "scan_run_id": scan_run_id,
            "batch_size": batch_size,
            "mode": "apply",
        },
    )


# ---------------------------------------------------------------------------
# Settings  /settings
# ---------------------------------------------------------------------------
@router.get("/settings", response_class=HTMLResponse, tags=["ui"])
async def settings_page(
    request: Request,
    session: Session = Depends(get_session),
):
    """Render the settings form pre-filled with current AppConfig."""
    config = get_config(session)
    templates = get_templates()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "config": config, "saved": False},
    )


@router.post("/settings", response_class=HTMLResponse, tags=["ui"])
async def save_settings(
    request: Request,
    session: Session = Depends(get_session),
    # Librarian — Radarr
    radarr_url: str = Form(""),
    radarr_api_key: str = Form(""),
    radarr_root_folder: str = Form("/movies"),
    radarr_folder_format: str = Form("{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}"),
    # Librarian — Sonarr
    sonarr_url: str = Form(""),
    sonarr_api_key: str = Form(""),
    sonarr_root_folder: str = Form("/tv"),
    sonarr_folder_format: str = Form("{Series TitleYear} {tvdb-{TvdbId}}"),
    # Librarian — General
    batch_size: int = Form(20),
    # Tracker
    radarr_tags: list[str] = Form([]),
    sonarr_tags: list[str] = Form([]),
    poll_interval_minutes: int = Form(15),
    max_concurrent_copies: int = Form(2),
    max_share_size_gb: float = Form(0.0),
    max_share_files: int = Form(0),
    share_path: str = Form("/share"),
    require_approval: str = Form(""),  # checkbox: "true" or ""
    # Notifications
    ntfy_url: str = Form("https://ntfy.sh"),
    ntfy_topic: str = Form(""),
    ntfy_token: str = Form(""),
    ntfy_on_copied: str = Form(""),
    ntfy_on_error: str = Form(""),
    ntfy_on_finished: str = Form(""),
    ntfy_on_first_run: str = Form(""),
):
    """Handle settings form POST — save config and re-render with success flag."""
    save_config(
        session,
        {
            "radarr_url": radarr_url,
            "radarr_api_key": radarr_api_key,
            "radarr_root_folder": radarr_root_folder,
            "radarr_folder_format": radarr_folder_format,
            "sonarr_url": sonarr_url,
            "sonarr_api_key": sonarr_api_key,
            "sonarr_root_folder": sonarr_root_folder,
            "sonarr_folder_format": sonarr_folder_format,
            "batch_size": batch_size,
            "radarr_tags": ", ".join(radarr_tags),
            "sonarr_tags": ", ".join(sonarr_tags),
            "poll_interval_minutes": poll_interval_minutes,
            "max_concurrent_copies": max_concurrent_copies,
            "max_share_size_gb": max_share_size_gb,
            "max_share_files": max_share_files,
            "share_path": share_path,
            "require_approval": require_approval == "true",
            "ntfy_url": ntfy_url,
            "ntfy_topic": ntfy_topic,
            "ntfy_token": ntfy_token,
            "ntfy_on_copied": ntfy_on_copied == "true",
            "ntfy_on_error": ntfy_on_error == "true",
            "ntfy_on_finished": ntfy_on_finished == "true",
            "ntfy_on_first_run": ntfy_on_first_run == "true",
        },
    )
    # Reschedule the poll loop with the new interval
    from app.scheduler import reschedule_poll
    reschedule_poll(poll_interval_minutes)
    config = get_config(session)
    templates = get_templates()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "config": config, "saved": True},
    )


# ---------------------------------------------------------------------------
# Logs  /logs
# ---------------------------------------------------------------------------
@router.get("/logs", response_class=HTMLResponse, tags=["ui"])
async def logs_page(request: Request):
    """Render the unified logs page (Renamer + Tracker tabs)."""
    from app.log_buffer import get_recent_logs
    recent_lines = log_buffer.tail(200)
    templates = get_templates()
    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "lines": recent_lines,
            "mode": "view",
        },
    )
