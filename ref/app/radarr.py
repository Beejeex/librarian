"""
Radarr API client for MadTracked.

Fetches movies tagged for tracking and resolves the tag name to a numeric ID.
Uses ArrClient for all HTTP and auth so no logic is duplicated with Sonarr.
"""

import logging
import os
from dataclasses import dataclass

from app.arr_client import ArrClient

logger = logging.getLogger(__name__)


@dataclass
class RadarrMovie:
    """Minimal movie data needed to create a TrackedItem."""

    movie_id: int
    movie_file_id: int  # Radarr's movieFile.id — changes on every upgrade/replacement
    title: str
    year: int
    file_path: str  # Absolute path to the movie file


class RadarrClient:
    """Fetches tagged movies from a Radarr instance."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._client = ArrClient(base_url, api_key)

    async def get_tagged_movies(self, tag_name: str) -> list[RadarrMovie]:
        """
        Return all movies in Radarr that have tag_name applied and have a file on disk.

        Args:
            tag_name: The tag label configured in MadTracked settings.
        """
        # --- Resolve tag name to ID ---
        tag_id = await self._client.resolve_tag_id(tag_name)
        if tag_id is None:
            return []

        movies = await self._client.get("/api/v3/movie")
        if not movies:
            return []

        results: list[RadarrMovie] = []
        for movie in movies:
            # Skip movies that don't have the configured tag
            if tag_id not in movie.get("tags", []):
                continue
            # Skip movies with no file — they haven't been downloaded yet
            movie_file = movie.get("movieFile")
            if not movie_file:
                logger.warning("Skipping '%s' — no movie file found.", movie.get("title"))
                continue

            results.append(
                RadarrMovie(
                    movie_id=movie["id"],
                    movie_file_id=movie_file["id"],
                    title=movie["title"],
                    year=movie.get("year", 0),
                    file_path=movie_file["path"],
                )
            )

        logger.info("Radarr: found %d tagged movie(s) with files.", len(results))
        return results
