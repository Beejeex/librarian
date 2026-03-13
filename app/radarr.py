"""
radarr.py — Radarr API client.

Fetches all movies and updates movie folder paths via the Radarr v3 REST API.
Path updates use GET-then-PUT to avoid sending partial objects.
Also provides tracker methods for fetching tagged movies.
"""

import logging
from dataclasses import dataclass

from app.arr_client import BaseArrClient
from app.naming import DEFAULT_MOVIE_FORMAT

logger = logging.getLogger(__name__)


@dataclass
class RadarrMovie:
    """Minimal movie data needed to create a TrackedItem."""

    movie_id: int
    movie_file_id: int  # Radarr's movieFile.id — changes on every upgrade/replacement
    title: str
    year: int
    file_path: str  # Absolute path to the movie file


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
        then PUTs the full object back. moveFiles=false ensures Radarr only
        updates its DB record — no physical file move is triggered.
        """
        movie = await self.get(f"/api/v3/movie/{movie_id}")
        movie["path"] = new_path
        await self.put(f"/api/v3/movie/{movie_id}?moveFiles=false", movie)
        logger.info("Radarr movie %s path updated to %s", movie_id, new_path)

    async def get_tagged_movies(self, tag_name: str) -> list[RadarrMovie]:
        """
        Return all movies in Radarr that have tag_name applied and have a file on disk.

        Args:
            tag_name: The tag label configured in Librarian settings.
        """
        tag_id = await self.resolve_tag_id(tag_name)
        if tag_id is None:
            return []

        movies = await self.get("/api/v3/movie")
        results: list[RadarrMovie] = []
        for movie in movies:
            if tag_id not in movie.get("tags", []):
                continue
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
