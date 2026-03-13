"""
sonarr.py — Sonarr API client.

Fetches all series and updates series folder paths via the Sonarr v3 REST API.
Path updates use GET-then-PUT to avoid sending partial objects.
Also provides tracker methods for fetching tagged episode files.
"""

import logging
from dataclasses import dataclass

from app.arr_client import BaseArrClient
from app.naming import DEFAULT_SERIES_FORMAT

logger = logging.getLogger(__name__)


@dataclass
class SonarrEpisodeFile:
    """Minimal episode file data needed to create a TrackedItem."""

    episode_file_id: int  # Used as source_id in TrackedItem
    series_id: int
    series_title: str
    season_number: int
    episode_number: int  # First episode number linked to this file
    title: str  # Human-readable label e.g. "Breaking Bad S01E01"
    file_path: str  # Absolute path to the episode file


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

    async def get_tagged_episode_files(self, tag_name: str) -> list[SonarrEpisodeFile]:
        """
        Return all episode files for series that have tag_name applied.

        Each episode file is returned as a separate SonarrEpisodeFile so the
        scheduler can track and copy them individually.

        Args:
            tag_name: The tag label configured in Librarian settings.
        """
        tag_id = await self.resolve_tag_id(tag_name)
        if tag_id is None:
            return []

        series_list = await self.get("/api/v3/series")
        tagged_series = [s for s in series_list if tag_id in s.get("tags", [])]
        if not tagged_series:
            logger.info("Sonarr: no series found with tag '%s'.", tag_name)
            return []

        results: list[SonarrEpisodeFile] = []
        for series in tagged_series:
            episode_files = await self.get(
                "/api/v3/episodefile", params={"seriesId": series["id"]}
            )
            if not episode_files:
                continue
            for ef in episode_files:
                ep_number, ep_label = await self._resolve_episode_meta(
                    ef["id"], series["title"], ef["seasonNumber"]
                )
                results.append(
                    SonarrEpisodeFile(
                        episode_file_id=ef["id"],
                        series_id=series["id"],
                        series_title=series["title"],
                        season_number=ef["seasonNumber"],
                        episode_number=ep_number,
                        title=ep_label,
                        file_path=ef["path"],
                    )
                )

        logger.info("Sonarr: found %d tagged episode file(s).", len(results))
        return results

    async def _resolve_episode_meta(
        self, episode_file_id: int, series_title: str, season_number: int
    ) -> tuple[int, str]:
        """
        Look up episode number and build a display title for an episode file.

        Falls back gracefully if the API call fails so a single bad episode
        doesn't break the entire poll run.

        Returns:
            (episode_number, display_title) — episode_number is 0 on failure.
        """
        episodes = await self.get(
            "/api/v3/episode", params={"episodeFileId": episode_file_id}
        )
        if not episodes:
            return 0, f"{series_title} S{season_number:02d}"
        ep = episodes[0]
        ep_number = ep.get("episodeNumber", 0)
        label = f"{series_title} S{season_number:02d}E{ep_number:02d}"
        return ep_number, label
