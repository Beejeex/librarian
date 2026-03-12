"""
main.py — FastAPI application entry point for Librarian.

Defines the app factory, startup/shutdown lifespan (DB init + connection pool cleanup),
router registration, static file serving, and Jinja2 template engine setup.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import create_db_and_tables, engine
from app.routers import api, ui

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# Shared Jinja2 templates instance — imported by routers
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.

    On startup:  create SQLite tables if they do not exist.
    On shutdown: dispose the SQLAlchemy connection pool cleanly (SIGTERM safe).
    """
    create_db_and_tables()
    yield
    engine.dispose()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Librarian",
    description="Radarr / Sonarr library folder standardiser",
    version="1.0.0",
    lifespan=lifespan,
    # Disable default Swagger/ReDoc in favour of the custom UI
    docs_url="/docs",
    redoc_url=None,
)

# Serve bundled JS assets (htmx, Alpine.js) — no CDN dependency
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Register routers
app.include_router(ui.router)
app.include_router(api.router)
