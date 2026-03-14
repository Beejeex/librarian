"""
routers/tags_ui.py — HTML page for Tag Management.

Routes:
  GET /tags — Tag management page (Movies / TV Shows tabs)
"""

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import load_config

logger = logging.getLogger(__name__)

router = APIRouter()
_templates_dir = os.path.join(os.path.dirname(__file__), "../templates")
templates = Jinja2Templates(directory=_templates_dir)


@router.get("/tags", response_class=HTMLResponse)
async def tags_page(request: Request) -> HTMLResponse:
    """Render the tag management page."""
    config = load_config()
    radarr_configured = bool(config.radarr_url and config.radarr_api_key)
    sonarr_configured = bool(config.sonarr_url and config.sonarr_api_key)
    return templates.TemplateResponse(
        "tags.html",
        {
            "request": request,
            "radarr_configured": radarr_configured,
            "sonarr_configured": sonarr_configured,
        },
    )
