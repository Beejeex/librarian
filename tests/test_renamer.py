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


def _make_item(db_session, scan_run_id, current_path, expected_folder, expected_path,
               source="radarr", disk_scenario="rename"):
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
        disk_scenario=disk_scenario,
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
        collision scenario: both old and new exist on disk — item is marked error,
        arr PUT is NOT called.
        """
        (tmp_media / "movies" / "NonExistent").mkdir(parents=True, exist_ok=True)
        (tmp_media / "movies" / "Target Folder").mkdir(parents=True, exist_ok=True)
        run = _make_scan_run(db_session)
        item = _make_item(
            db_session,
            run.id,
            current_path="/movies/NonExistent",
            expected_folder="Target Folder",
            expected_path="/movies/Target Folder",
            disk_scenario="collision",
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
        If arr PUT fails after disk rename, item is error with explicit 'disk is correct' message.
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
            disk_scenario="rename",
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
        # item 1: collision scenario → will error
        item1 = _make_item(
            db_session, run.id,
            current_path="/movies/NonExistent",
            expected_folder="Target1",
            expected_path="/movies/Target1",
            disk_scenario="collision",
        )
        # item 2: normal rename scenario → should succeed
        item2 = _make_item(
            db_session, run.id,
            current_path="/movies/GoodMovie",
            expected_folder="GoodMovie (2020) {tmdb-999}",
            expected_path="/movies/GoodMovie (2020) {tmdb-999}",
            disk_scenario="rename",
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

    async def test_arr_only_skips_disk_renames_updates_arr(self, db_session, sample_config, tmp_media):
        """
        arr_only scenario: disk already has expected folder — no os.rename, arr still updated.
        """
        # Only the EXPECTED folder exists — old name is gone
        (tmp_media / "movies" / "Dune (2021) {tmdb-438631}").mkdir(parents=True, exist_ok=True)

        run = _make_scan_run(db_session)
        item = _make_item(
            db_session, run.id,
            current_path="/movies/Dune.2021.2160p",
            expected_folder="Dune (2021) {tmdb-438631}",
            expected_path="/movies/Dune (2021) {tmdb-438631}",
            disk_scenario="arr_only",
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
        mock_client.update_movie_path.assert_called_once()
        # Expected folder must still exist (arr_only does not touch disk)
        assert (tmp_media / "movies" / "Dune (2021) {tmdb-438631}").exists()

    async def test_collision_scenario_marks_error_no_rename(self, db_session, sample_config, tmp_media):
        """
        collision scenario: both old and new folder exist — must error, no rename.
        """
        (tmp_media / "movies" / "OldName").mkdir(parents=True, exist_ok=True)
        (tmp_media / "movies" / "NewName").mkdir(parents=True, exist_ok=True)

        run = _make_scan_run(db_session)
        item = _make_item(
            db_session, run.id,
            current_path="/movies/OldName",
            expected_folder="NewName",
            expected_path="/movies/NewName",
            disk_scenario="collision",
        )

        mock_client = AsyncMock()

        with (
            patch("app.renamer.MEDIA_MOUNTS", {"radarr": str(tmp_media / "movies"), "sonarr": str(tmp_media / "tv")}),
            patch("app.renamer.get_radarr_client", return_value=mock_client),
        ):
            await run_apply(run.id, 10, db_session, sample_config)

        db_session.refresh(item)
        assert item.status == "error"
        assert "collision" in item.error_message.lower() or "both" in item.error_message.lower()
        mock_client.update_movie_path.assert_not_called()

    async def test_missing_scenario_updates_arr_marks_done(self, db_session, sample_config, tmp_media):
        """
        missing scenario (different names): folder absent from disk — arr path is still
        updated (arr-only) and item is marked done. No disk rename attempted.
        """
        run = _make_scan_run(db_session)
        item = _make_item(
            db_session, run.id,
            current_path="/movies/Phantom",
            expected_folder="Phantom (2023) {tmdb-111}",
            expected_path="/movies/Phantom (2023) {tmdb-111}",
            disk_scenario="missing",
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
        mock_client.update_movie_path.assert_called_once()


# ---------------------------------------------------------------------------
# File-type RenameItems (_process_file_item)
# ---------------------------------------------------------------------------
def _make_file_item(db_session, scan_run_id, source="radarr", source_file_id=42):
    """Create an approved RenameItem with item_type='file'."""
    item = RenameItem(
        scan_run_id=scan_run_id,
        source=source,
        source_id=1,
        source_file_id=source_file_id,
        title="Dune.2021.old.mkv",
        current_folder="Dune.2021.old.mkv",
        expected_folder="Dune (2021).mkv",
        current_path="/movies/Dune (2021) {tmdb-438631}/Dune.2021.old.mkv",
        expected_path="/movies/Dune (2021) {tmdb-438631}/Dune (2021).mkv",
        status="approved",
        disk_scenario="unknown",
        item_type="file",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


class TestProcessFileItem:
    async def test_happy_path_marks_done(self, db_session, sample_config):
        """arr rename command succeeds → item status = done."""
        run = _make_scan_run(db_session)
        item = _make_file_item(db_session, run.id)

        mock_client = AsyncMock()
        mock_client.command_rename_files = AsyncMock()

        with patch("app.renamer.get_radarr_client", return_value=mock_client):
            await run_apply(run.id, 10, db_session, sample_config)

        db_session.refresh(item)
        assert item.status == "done"
        mock_client.command_rename_files.assert_called_once_with(1, [42])

    async def test_missing_source_file_id_marks_error(self, db_session, sample_config):
        """item_type='file' with source_file_id=None → error, no arr call."""
        run = _make_scan_run(db_session)
        item = _make_file_item(db_session, run.id, source_file_id=None)

        mock_client = AsyncMock()

        with patch("app.renamer.get_radarr_client", return_value=mock_client):
            await run_apply(run.id, 10, db_session, sample_config)

        db_session.refresh(item)
        assert item.status == "error"
        assert "source_file_id" in item.error_message
        mock_client.command_rename_files.assert_not_called()

    async def test_arr_command_failure_marks_error(self, db_session, sample_config):
        """command_rename_files raises → item status = error with message."""
        run = _make_scan_run(db_session)
        item = _make_file_item(db_session, run.id)

        mock_client = AsyncMock()
        mock_client.command_rename_files = AsyncMock(side_effect=Exception("API down"))

        with patch("app.renamer.get_radarr_client", return_value=mock_client):
            await run_apply(run.id, 10, db_session, sample_config)

        db_session.refresh(item)
        assert item.status == "error"
        assert "API down" in item.error_message

    async def test_file_item_does_not_touch_disk(self, db_session, sample_config, tmp_media):
        """File items must not trigger os.rename — arr handles the physical file."""
        run = _make_scan_run(db_session)
        item = _make_file_item(db_session, run.id, source="sonarr")

        mock_client = AsyncMock()
        mock_client.command_rename_files = AsyncMock()

        # Snapshot existing tv dir contents before apply
        tv_dir = tmp_media / "tv"
        before = set(tv_dir.iterdir()) if tv_dir.exists() else set()

        with (
            patch("app.renamer.MEDIA_MOUNTS", {"radarr": str(tmp_media / "movies"), "sonarr": str(tmp_media / "tv")}),
            patch("app.renamer.get_sonarr_client", return_value=mock_client),
            patch("app.renamer.get_radarr_client", return_value=mock_client),
        ):
            await run_apply(run.id, 10, db_session, sample_config)

        db_session.refresh(item)
        assert item.status == "done"
        mock_client.command_rename_files.assert_called_once_with(1, [42])
        # No new entries should have appeared on disk (no os.rename called)
        after = set(tv_dir.iterdir()) if tv_dir.exists() else set()
        assert after == before
