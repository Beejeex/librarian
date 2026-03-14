"""
test_scanner.py — Tests for app/scanner.py (run_scan).

Verifies that:
- Items whose current folder already matches expected are excluded.
- Mismatches are written to the DB as pending RenameItems.
- Re-scanning clears previous non-done items and rebuilds the list.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.models import RenameItem, ScanRun
from app.scanner import run_scan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_radarr_client(movies):
    """Return a mock RadarrClient whose fetch_movies() yields `movies`."""
    client = AsyncMock()
    client.fetch_movies = AsyncMock(return_value=movies)
    return client


def _make_sonarr_client(series):
    """Return a mock SonarrClient whose fetch_series() yields `series`."""
    client = AsyncMock()
    client.fetch_series = AsyncMock(return_value=series)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestRunScanRadarr:
    async def test_mismatch_creates_pending_item(self, db_session, sample_config):
        """A movie whose folder doesn't match expected becomes a pending RenameItem."""
        movies = [
            {
                "id": 1,
                "title": "Dune",
                "year": 2021,
                "tmdbId": 438631,
                "path": "/movies/Dune.2021.2160p",
            }
        ]
        with (
            patch("app.scanner.get_radarr_client", return_value=_make_radarr_client(movies)),
        ):
            scan_run = await run_scan("radarr", db_session, sample_config)

        assert scan_run.total_items == 1
        assert scan_run.status == "ready"

        items = db_session.exec(select(RenameItem)).all()
        assert len(items) == 1
        assert items[0].status == "pending"
        assert items[0].expected_folder == "Dune (2021) {tmdb-438631}"
        assert items[0].current_folder == "Dune.2021.2160p"

    async def test_matching_folder_excluded(self, db_session, sample_config):
        """A movie that already matches the expected name is not written to DB.

        In tests MEDIA_MOUNTS has no real paths so disk check is skipped and the
        item is excluded (scan_run.total_items == 0).
        """
        movies = [
            {
                "id": 2,
                "title": "Interstellar",
                "year": 2014,
                "tmdbId": 157336,
                "path": "/movies/Interstellar (2014) {tmdb-157336}",
            }
        ]
        # Patch MEDIA_MOUNTS so the disk-existence check is skipped
        with patch("app.scanner.get_radarr_client", return_value=_make_radarr_client(movies)), \
             patch("app.scanner.MEDIA_MOUNTS", {}):
            scan_run = await run_scan("radarr", db_session, sample_config)

        assert scan_run.total_items == 0
        items = db_session.exec(select(RenameItem)).all()
        assert len(items) == 0

    async def test_rescan_clears_previous_pending(self, db_session, sample_config):
        """Re-scanning replaces pending items from the previous scan."""
        movies = [
            {
                "id": 1,
                "title": "Dune",
                "year": 2021,
                "tmdbId": 438631,
                "path": "/movies/Dune.2021.2160p",
            }
        ]

        with patch("app.scanner.get_radarr_client", return_value=_make_radarr_client(movies)):
            await run_scan("radarr", db_session, sample_config)
            # second scan — should clear old pending and start fresh
            scan_run2 = await run_scan("radarr", db_session, sample_config)

        items = db_session.exec(
            select(RenameItem).where(RenameItem.scan_run_id == scan_run2.id)
        ).all()
        assert len(items) == 1  # only one item, not two duplicates

    async def test_item_with_zero_tmdbid_skipped(self, db_session, sample_config):
        """Movies with tmdbId=0 are skipped — no valid ID to build expected name."""
        movies = [
            {
                "id": 5,
                "title": "Unknown",
                "year": 2020,
                "tmdbId": 0,
                "path": "/movies/Unknown.2020",
            }
        ]
        with patch("app.scanner.get_radarr_client", return_value=_make_radarr_client(movies)):
            scan_run = await run_scan("radarr", db_session, sample_config)

        assert scan_run.total_items == 0

    async def test_all_mismatches_collected(self, db_session, sample_config):
        """Scan finds all mismatches regardless of batch_size — batch_size only limits apply."""
        # sample_config.batch_size == 10; create 12 distinct mismatching movies
        movies = [
            {
                "id": i,
                "title": f"Movie {i}",
                "year": 2000 + i,
                "tmdbId": 100 + i,
                "path": f"/movies/Movie.{i}.old",
            }
            for i in range(1, 13)  # 12 items, all mismatching
        ]
        with patch("app.scanner.get_radarr_client", return_value=_make_radarr_client(movies)):
            scan_run = await run_scan("radarr", db_session, sample_config)

        assert scan_run.total_items == 12  # all found, not capped at batch_size
        items = db_session.exec(select(RenameItem)).all()
        assert len(items) == 12


class TestRunScanSonarr:
    async def test_mismatch_creates_pending_item(self, db_session, sample_config):
        """A series whose folder doesn't match expected becomes a pending RenameItem."""
        series = [
            {
                "id": 1,
                "title": "Breaking Bad",
                "year": 2008,
                "tvdbId": 81189,
                "path": "/tv/Breaking.Bad.S01",
            }
        ]
        with patch("app.scanner.get_sonarr_client", return_value=_make_sonarr_client(series)):
            scan_run = await run_scan("sonarr", db_session, sample_config)

        assert scan_run.total_items == 1
        items = db_session.exec(select(RenameItem)).all()
        assert len(items) == 1
        assert items[0].expected_folder == "Breaking Bad (2008) {tvdb-81189}"

    async def test_item_with_zero_tvdbid_skipped(self, db_session, sample_config):
        """Series with tvdbId=0 are skipped."""
        series = [
            {
                "id": 9,
                "title": "Pilot",
                "year": 2010,
                "tvdbId": 0,
                "path": "/tv/Pilot.2010",
            }
        ]
        with patch("app.scanner.get_sonarr_client", return_value=_make_sonarr_client(series)):
            scan_run = await run_scan("sonarr", db_session, sample_config)

        assert scan_run.total_items == 0
