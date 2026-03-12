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
from app.database import get_session
from app.log_buffer import log_buffer
from app.models import RenameItem, ScanRun

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

    templates = get_templates()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "radarr_run": radarr_run,
            "sonarr_run": sonarr_run,
            "config": config,
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
    Render the review page: mismatch table with approve/skip toggles.

    Defaults to showing the latest scan run for the given source.
    """
    config = get_config(session)

    scan_run = session.exec(
        select(ScanRun)
        .where(ScanRun.source == source)
        .order_by(ScanRun.id.desc())  # type: ignore[arg-type]
        .limit(1)
    ).first()

    items: list[RenameItem] = []
    if scan_run:
        items = list(
            session.exec(
                select(RenameItem)
                .where(
                    RenameItem.scan_run_id == scan_run.id,
                    RenameItem.status.in_(("pending", "approved", "skipped")),  # type: ignore[attr-defined]
                )
                .order_by(RenameItem.id)  # type: ignore[arg-type]
            ).all()
        )

    templates = get_templates()
    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "source": source,
            "scan_run": scan_run,
            "items": items,
            "batch_size": config.batch_size,
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
    radarr_url: str = Form(""),
    radarr_api_key: str = Form(""),
    radarr_root_folder: str = Form("/movies"),
    radarr_folder_format: str = Form("{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}"),
    sonarr_url: str = Form(""),
    sonarr_api_key: str = Form(""),
    sonarr_root_folder: str = Form("/tv"),
    sonarr_folder_format: str = Form("{Series TitleYear} {tvdb-{TvdbId}}"),
    batch_size: int = Form(20),
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
        },
    )
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
    """Render the logs page showing cached recent log output."""
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
