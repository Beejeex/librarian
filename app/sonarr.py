"""
sonarr.py — Sonarr API client.

Fetches all series and updates series folder paths via the Sonarr v3 REST API.
Path updates use GET-then-PUT to avoid sending partial objects.
"""

import logging

from app.arr_client import BaseArrClient
from app.naming import DEFAULT_SERIES_FORMAT

logger = logging.getLogger(__name__)


class SonarrClient(BaseArrClient):
    """Client for the Sonarr v3 API."""

    async def fetch_series(self) -> list[dict]:
        """
        Fetch all series from Sonarr.
        Returns a list of full series objects from GET /api/v3/series.
        """
        return await self.get("/api/v3/series")

    async def fetch_folder_format(self) -> str:
        """
        Fetch the series folder naming format from Sonarr's naming config.
        Returns the seriesFolderFormat string, or the default if not present.
        """
        data = await self.get("/api/v3/config/naming")
        return data.get("seriesFolderFormat", DEFAULT_SERIES_FORMAT)  # type: ignore[union-attr]

    async def update_series_path(self, series_id: int, new_path: str) -> None:
        """
        Update the folder path for a series in Sonarr's database.

        Performs a GET to retrieve the full object, modifies only the path,
        then PUTs the full object back. moveFiles=false ensures Sonarr only
        updates its DB record — no physical file move is triggered.
        """
        series = await self.get(f"/api/v3/series/{series_id}")
        series["path"] = new_path
        await self.put(f"/api/v3/series/{series_id}?moveFiles=false", series)
        logger.info("Sonarr series %s path updated to %s", series_id, new_path)
