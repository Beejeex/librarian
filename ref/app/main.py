"""
FastAPI application entry point for MadTracked.

Manages the full application lifecycle:
- Creates DB tables on startup.
- Starts the APScheduler poll loop and watchdog share monitor.
- Shuts everything down cleanly on SIGTERM so no partial writes occur.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import create_db_and_tables, run_migrations
from app.config import load_config, mask_secrets
from app.scheduler import start_scheduler, stop_scheduler
from app.watcher import start_watcher, stop_watcher
from app.routers import ui, api
from app.log_buffer import setup_memory_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage startup and shutdown of background services.

    Order matters: scheduler and watcher are started after the DB is ready,
    and stopped before the DB connection pool closes.
    """
    # --- Startup ---
    setup_memory_handler()  # Capture all log output for the UI /logs page
    create_db_and_tables()
    run_migrations()

    # Reset any items stuck in 'copying' from a previous crash or restart
    from app.database import get_session
    from app.models import TrackedItem
    from sqlmodel import select
    from datetime import datetime, timezone
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
            logger.warning("Reset %d stuck 'copying' item(s) to 'pending'.", len(stuck))

    config = load_config()
    logger.info("Config loaded: %s", mask_secrets(config))

    start_scheduler(config)
    start_watcher(config.share_path)
    logger.info("MadTracked started. Listening on :8080")

    yield  # Application runs here

    # --- Shutdown ---
    stop_scheduler()
    stop_watcher()
    logger.info("MadTracked shut down cleanly.")


app = FastAPI(title="MadTracked", lifespan=lifespan)

# Serve bundled JS assets without relying on external CDNs
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Mount routers
app.include_router(ui.router)
app.include_router(api.router, prefix="/api")


@app.get("/health")
def health_check() -> dict:
    """Return a simple liveness response used by Docker HEALTHCHECK."""
    return {"status": "ok"}
