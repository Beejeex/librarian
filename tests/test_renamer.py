"""
test_renamer.py — Tests for app/renamer.py.

Verifies:
- remap_to_container() translates arr paths to container-local paths.
- Folder rename on disk succeeds and status becomes 'done'.
- Disk rename failure marks item 'error' and arr PUT is not called.
- arr update failure after disk rename marks item 'error' with a clear message.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from app.models import RenameItem, ScanRun
from app.renamer import remap_to_container, run_apply


# ---------------------------------------------------------------------------
# remap_to_container
# ---------------------------------------------------------------------------
class TestRemapToContainer:
    def test_basic_remap(self):
        result = remap_to_container("/movies/Dune.2021", "/movies", "/media/movies")
        assert result == "/media/movies/Dune.2021"

    def test_tv_remap(self):
        result = remap_to_container("/tv/Breaking.Bad", "/tv", "/media/tv")
        assert result == "/media/tv/Breaking.Bad"

    def test_root_folder_trailing_slash_ignored(self):
        result = remap_to_container("/movies/Dune", "/movies/", "/media/movies")
        assert result == "/media/movies/Dune"

    def test_raises_if_path_does_not_match_root(self):
        with pytest.raises(ValueError):
            remap_to_container("/other/Dune", "/movies", "/media/movies")


# ---------------------------------------------------------------------------
# run_apply helpers
# ---------------------------------------------------------------------------
def _make_scan_run(db_session):
    """Create a ScanRun in 'ready' state and return it."""
    run = ScanRun(source="radarr", status="ready", total_items=1, done_count=0, error_count=0)
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def _make_item(db_session, scan_run_id, current_path, expected_folder, expected_path, source="radarr"):
    item = RenameItem(
        scan_run_id=scan_run_id,
        source=source,
        source_id=1,
        title="Test Movie",
        current_folder=os.path.basename(current_path),
        expected_folder=expected_folder,
        current_path=current_path,
        expected_path=expected_path,
        status="approved",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestRunApply:
    async def test_successful_rename(self, db_session, sample_config, tmp_media):
        """
        Full happy-path: disk rename succeeds + arr PUT succeeds → status = done.

        The test monkeypatches MEDIA_MOUNTS and mocks the arr client so no
        real filesystem path (/media/movies) is needed.
        """
        # Create actual folder in tmp_media
        old_folder = tmp_media / "movies" / "Dune.2021.2160p"
        old_folder.mkdir(parents=True, exist_ok=True)

        run = _make_scan_run(db_session)
        item = _make_item(
            db_session,
            run.id,
            current_path="/movies/Dune.2021.2160p",
            expected_folder="Dune (2021) {tmdb-438631}",
            expected_path="/movies/Dune (2021) {tmdb-438631}",
        )

        mock_client = AsyncMock()
        mock_client.update_movie_path = AsyncMock()

        with (
            patch("app.renamer.MEDIA_MOUNTS", {"radarr": str(tmp_media / "movies"), "sonarr": str(tmp_media / "tv")}),
            patch("app.renamer.get_radarr_client", return_value=mock_client),
        ):
            await run_apply(run.id, 10, db_session, sample_config)

        db_session.refresh(item)
        assert item.status == "done"
        assert (tmp_media / "movies" / "Dune (2021) {tmdb-438631}").exists()

    async def test_disk_rename_failure_marks_error(self, db_session, sample_config, tmp_media):
        """
        If os.rename raises, item is marked error and arr PUT is NOT called.
        """
        run = _make_scan_run(db_session)
        item = _make_item(
            db_session,
            run.id,
            current_path="/movies/NonExistent",
            expected_folder="Target Folder",
            expected_path="/movies/Target Folder",
        )

        mock_client = AsyncMock()

        with (
            patch("app.renamer.MEDIA_MOUNTS", {"radarr": str(tmp_media / "movies"), "sonarr": str(tmp_media / "tv")}),
            patch("app.renamer.get_radarr_client", return_value=mock_client),
        ):
            await run_apply(run.id, 10, db_session, sample_config)

        db_session.refresh(item)
        assert item.status == "error"
        assert item.error_message is not None
        mock_client.update_movie_path.assert_not_called()

    async def test_arr_update_failure_marks_error_with_message(self, db_session, sample_config, tmp_media):
        """
        If arr PUT fails after disk rename, item is error with explicit 'disk was renamed' message.
        """
        old_folder = tmp_media / "movies" / "OldName"
        old_folder.mkdir(parents=True, exist_ok=True)

        run = _make_scan_run(db_session)
        item = _make_item(
            db_session,
            run.id,
            current_path="/movies/OldName",
            expected_folder="NewName",
            expected_path="/movies/NewName",
        )

        mock_client = AsyncMock()
        mock_client.update_movie_path = AsyncMock(side_effect=Exception("API down"))

        with (
            patch("app.renamer.MEDIA_MOUNTS", {"radarr": str(tmp_media / "movies"), "sonarr": str(tmp_media / "tv")}),
            patch("app.renamer.get_radarr_client", return_value=mock_client),
        ):
            await run_apply(run.id, 10, db_session, sample_config)

        db_session.refresh(item)
        assert item.status == "error"
        assert "disk" in item.error_message.lower() or "renamed" in item.error_message.lower()
        # Folder was renamed on disk
        assert (tmp_media / "movies" / "NewName").exists()

    async def test_single_item_error_does_not_abort_batch(self, db_session, sample_config, tmp_media):
        """Error on item 1 must not prevent item 2 from being processed."""
        (tmp_media / "movies" / "GoodMovie").mkdir(parents=True, exist_ok=True)

        run = _make_scan_run(db_session)
        # item 1: non-existent folder → disk rename will fail
        item1 = _make_item(
            db_session, run.id,
            current_path="/movies/NonExistent",
            expected_folder="Target1",
            expected_path="/movies/Target1",
        )
        # item 2: existing folder → should succeed
        item2 = _make_item(
            db_session, run.id,
            current_path="/movies/GoodMovie",
            expected_folder="GoodMovie (2020) {tmdb-999}",
            expected_path="/movies/GoodMovie (2020) {tmdb-999}",
        )

        mock_client = AsyncMock()
        mock_client.update_movie_path = AsyncMock()

        with (
            patch("app.renamer.MEDIA_MOUNTS", {"radarr": str(tmp_media / "movies"), "sonarr": str(tmp_media / "tv")}),
            patch("app.renamer.get_radarr_client", return_value=mock_client),
        ):
            await run_apply(run.id, 10, db_session, sample_config)

        db_session.refresh(item1)
        db_session.refresh(item2)
        assert item1.status == "error"
        assert item2.status == "done"
