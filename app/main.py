"""
main.py — FastAPI application entry point for Librarian.

Defines the app factory, startup/shutdown lifespan (DB init + scheduler +
watchdog + connection pool cleanup), router registration, static file serving,
and Jinja2 template engine setup.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from app.version import VERSION

import logging

from app.config import load_config
from app.database import create_db_and_tables, engine, get_session
from app.log_buffer import LogHandler, log_buffer
from app.models import TrackedItem
from app.routers import api, ui
from app.routers import tracker_api, tracker_ui
from app.scheduler import start_scheduler, stop_scheduler
from app.watcher import start_watcher, stop_watcher

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# Shared Jinja2 templates instance — imported by routers
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["VERSION"] = VERSION


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.

    On startup:
      1. Create SQLite tables if they do not exist.
      2. Reset any items stuck in 'copying' status (interrupted mid-copy).
      3. Start the APScheduler tracker poll loop.
      4. Start the watchdog observer to monitor /share for deletions.

    On shutdown:
      1. Stop the APScheduler.
      2. Stop the watchdog observer.
      3. Dispose the SQLAlchemy connection pool cleanly (SIGTERM safe).
    """
    create_db_and_tables()

    # Install log handler so scheduler/watcher output flows into the SSE stream
    handler = LogHandler(log_buffer, level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s"))
    logging.getLogger("app").addHandler(handler)

    # Reset items stuck in 'copying' from a previous container restart
    with get_session() as session:
        stuck = session.exec(
            select(TrackedItem).where(TrackedItem.status == "copying")
        ).all()
        for item in stuck:
            item.status = "pending"
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
        if stuck:
            session.commit()

    config = load_config()
    start_scheduler(config)
    start_watcher(config.share_path)

    yield

    stop_scheduler()
    stop_watcher()
    engine.dispose()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Librarian",
    description="Radarr / Sonarr library manager with media tracker",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

# Serve bundled JS assets (htmx, Alpine.js) — no CDN dependency
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Register routers
app.include_router(ui.router)
app.include_router(api.router)
app.include_router(tracker_ui.router)
app.include_router(tracker_api.router)


@app.get("/health")
async def health() -> JSONResponse:
    """Health check endpoint for Docker HEALTHCHECK."""
    return JSONResponse({"status": "ok"})

