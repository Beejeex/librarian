"""
radarr.py — Radarr API client.

Fetches all movies and updates movie folder paths via the Radarr v3 REST API.
Path updates use GET-then-PUT to avoid sending partial objects.
"""

import logging

from app.arr_client import BaseArrClient
from app.naming import DEFAULT_MOVIE_FORMAT

logger = logging.getLogger(__name__)


class RadarrClient(BaseArrClient):
    """Client for the Radarr v3 API."""

    async def fetch_movies(self) -> list[dict]:
        """
        Fetch all movies from Radarr.
        Returns a list of full movie objects from GET /api/v3/movie.
        """
        return await self.get("/api/v3/movie")

    async def fetch_folder_format(self) -> str:
        """
        Fetch the movie folder naming format from Radarr's naming config.
        Returns the movieFolderFormat string, or the default if not present.
        """
        data = await self.get("/api/v3/config/naming")
        return data.get("movieFolderFormat", DEFAULT_MOVIE_FORMAT)  # type: ignore[union-attr]

    async def update_movie_path(self, movie_id: int, new_path: str) -> None:
        """
        Update the folder path for a movie in Radarr's database.

        Performs a GET to retrieve the full object, modifies only the path,
        then PUTs the full object back. This prevents missing-required-field errors.
        No file move is triggered — Radarr only updates its DB record.
        """
        movie = await self.get(f"/api/v3/movie/{movie_id}")
        movie["path"] = new_path
        await self.put(f"/api/v3/movie/{movie_id}", movie)
        logger.info("Radarr movie %s path updated to %s", movie_id, new_path)
