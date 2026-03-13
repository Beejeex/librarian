"""
Sonarr API client for MadTracked.

Fetches episode files for tagged series. Each episode file becomes its own
TrackedItem so deletions from the share can be tracked individually.
Uses ArrClient for all HTTP and auth so no logic is duplicated with Radarr.
"""

import logging
from dataclasses import dataclass

from app.arr_client import ArrClient

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


class SonarrClient:
    """Fetches tagged series and their episode files from a Sonarr instance."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._client = ArrClient(base_url, api_key)

    async def get_tagged_episode_files(self, tag_name: str) -> list[SonarrEpisodeFile]:
        """
        Return all episode files for series that have tag_name applied.

        Each episode file is returned as a separate SonarrEpisodeFile so the
        scheduler can track and copy them individually.

        Args:
            tag_name: The tag label configured in MadTracked settings.
        """
        # --- Resolve tag name to ID ---
        tag_id = await self._client.resolve_tag_id(tag_name)
        if tag_id is None:
            return []

        series_list = await self._client.get("/api/v3/series")
        if not series_list:
            return []

        # Filter to only series carrying the configured tag
        tagged_series = [s for s in series_list if tag_id in s.get("tags", [])]
        if not tagged_series:
            logger.info("Sonarr: no series found with tag '%s'.", tag_name)
            return []

        results: list[SonarrEpisodeFile] = []
        for series in tagged_series:
            episode_files = await self._client.get(
                "/api/v3/episodefile", params={"seriesId": series["id"]}
            )
            if not episode_files:
                continue

            for ef in episode_files:
                # Resolve episode metadata to get episode number and a display title
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
        episodes = await self._client.get(
            "/api/v3/episode", params={"episodeFileId": episode_file_id}
        )
        if not episodes:
            # Fallback: no episode metadata available
            return 0, f"{series_title} S{season_number:02d}"

        # A single file can cover multiple episodes; use the first one
        ep = episodes[0]
        ep_number = ep.get("episodeNumber", 0)
        label = f"{series_title} S{season_number:02d}E{ep_number:02d}"
        return ep_number, label
