"""
routers/tags_api.py — Tag management API endpoints.

Provides:
  GET  /api/tags/{source}/data         — all items + all tags as JSON
  POST /api/tags/{source}/tag          — create a new tag in the arr app
  POST /api/tags/{source}/items/update — bulk add/remove tags on selected items
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_config
from app.radarr import RadarrClient
from app.sonarr import SonarrClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tags")

# Lazy semaphore — created inside the event loop on first bulk request
_bulk_sem: asyncio.Semaphore | None = None


def _get_bulk_sem() -> asyncio.Semaphore:
    global _bulk_sem
    if _bulk_sem is None:
        _bulk_sem = asyncio.Semaphore(5)
    return _bulk_sem


def _get_client(source: str) -> RadarrClient | SonarrClient:
    if source not in ("radarr", "sonarr"):
        raise HTTPException(status_code=400, detail="source must be 'radarr' or 'sonarr'")
    config = load_config()
    if source == "radarr":
        if not config.radarr_url or not config.radarr_api_key:
            raise HTTPException(status_code=400, detail="Radarr is not configured")
        return RadarrClient(config.radarr_url, config.radarr_api_key)
    if not config.sonarr_url or not config.sonarr_api_key:
        raise HTTPException(status_code=400, detail="Sonarr is not configured")
    return SonarrClient(config.sonarr_url, config.sonarr_api_key)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@router.get("/{source}/data")
async def get_tag_data(source: str) -> dict:
    """Return all items and all available tags for the given arr source."""
    client = _get_client(source)
    if source == "radarr":
        tags, items = await asyncio.gather(
            client.fetch_tags(),
            client.fetch_movies_with_tags(),
        )
    else:
        tags, items = await asyncio.gather(
            client.fetch_tags(),
            client.fetch_series_with_tags(),
        )
    return {"tags": tags, "items": items}


# ---------------------------------------------------------------------------
# Create tag
# ---------------------------------------------------------------------------

class CreateTagRequest(BaseModel):
    label: str


@router.post("/{source}/tag")
async def create_tag(source: str, body: CreateTagRequest) -> dict:
    """Create a new tag in the arr app and return {id, label}."""
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="label must not be empty")
    client = _get_client(source)
    return await client.create_tag(label)


# ---------------------------------------------------------------------------
# Bulk update
# ---------------------------------------------------------------------------

class UpdateTagsRequest(BaseModel):
    item_ids: list[int]
    add_labels: list[str] = []
    remove_labels: list[str] = []


@router.post("/{source}/items/update")
async def update_item_tags(source: str, body: UpdateTagsRequest) -> dict:
    """
    Bulk add/remove tags on a set of items.

    add_labels and remove_labels are tag name strings (not IDs).
    Unknown labels are silently ignored.
    Returns {updated: int, errors: [{id, error}]}.
    """
    client = _get_client(source)
    tags = await client.fetch_tags()
    label_to_id: dict[str, int] = {t["label"]: t["id"] for t in tags}

    add_ids: set[int] = {label_to_id[l] for l in body.add_labels if l in label_to_id}
    remove_ids: set[int] = {label_to_id[l] for l in body.remove_labels if l in label_to_id}

    sem = _get_bulk_sem()

    async def _update_one(item_id: int) -> dict | None:
        async with sem:
            try:
                if source == "radarr":
                    await client.update_movie_tags(item_id, add_ids, remove_ids)
                else:
                    await client.update_series_tags(item_id, add_ids, remove_ids)
                return None
            except Exception as exc:
                logger.error("Tag update failed for %s %s: %s", source, item_id, exc)
                return {"id": item_id, "error": str(exc)}

    results = await asyncio.gather(*(_update_one(iid) for iid in body.item_ids))
    errors = [r for r in results if r is not None]
    return {"updated": len(body.item_ids) - len(errors), "errors": errors}
