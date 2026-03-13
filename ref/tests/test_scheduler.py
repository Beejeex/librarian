"""
Unit tests for the poll scheduler logic (app/scheduler.py).

Tests item deduplication, status transitions, first-run mode,
approval gating, and copy error handling.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.models import AppConfig, TrackedItem
from app.scheduler import _process_item
import app.database as db_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(
    *,
    radarr_first_run_complete: bool = True,
    sonarr_first_run_complete: bool = True,
    require_approval: bool = False,
) -> AppConfig:
    """Return a minimal AppConfig for scheduler unit tests."""
    return AppConfig(
        radarr_url="", radarr_api_key="", radarr_tags="",
        sonarr_url="", sonarr_api_key="", sonarr_tags="",
        copy_mode="copy",
        share_path="/share",
        radarr_first_run_complete=radarr_first_run_complete,
        sonarr_first_run_complete=sonarr_first_run_complete,
        require_approval=require_approval,
        max_concurrent_copies=2,
        max_share_size_gb=0,
        max_share_files=0,
    )


@pytest.fixture()
def test_engine():
    """In-memory SQLite engine — tables created fresh per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    original = db_module.engine
    db_module.engine = engine
    yield engine
    db_module.engine = original
    SQLModel.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Normal-mode tests (first_run_complete=True, require_approval=False)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_item_is_copied_and_recorded(test_engine, tmp_share):
    """A previously unseen item should be copied and stored with status 'copied'."""
    src = tmp_share["sample_file"]
    dest = tmp_share["share"] + "/Movie (2024)/movie.mkv"
    config = make_config()
    semaphore = asyncio.Semaphore(2)

    with patch("app.scheduler.copy_file", new_callable=AsyncMock), \
         patch("app.scheduler.get_file_size", return_value=1000), \
         patch("app.scheduler.check_quota", return_value=True):
        await _process_item(
            config=config, semaphore=semaphore,
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest, tag="share",
        )

    with Session(test_engine) as session:
        item = session.exec(select(TrackedItem)).first()
    assert item is not None
    assert item.status == "copied"


@pytest.mark.asyncio
async def test_already_copied_item_is_not_re_copied(test_engine, tmp_share):
    """An item already in DB with status 'copied' must not trigger another copy."""
    src = tmp_share["sample_file"]
    dest = tmp_share["share"] + "/Movie (2024)/movie.mkv"
    config = make_config()
    semaphore = asyncio.Semaphore(2)

    with Session(test_engine) as session:
        session.add(TrackedItem(
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest,
            status="copied", tag="share",
        ))
        session.commit()

    with patch("app.scheduler.copy_file", new_callable=AsyncMock) as mock_copy:
        await _process_item(
            config=config, semaphore=semaphore,
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest, tag="share",
        )
        mock_copy.assert_not_called()


@pytest.mark.asyncio
async def test_finished_item_is_never_re_copied(test_engine, tmp_share):
    """An item with status 'finished' must be permanently skipped."""
    src = tmp_share["sample_file"]
    dest = tmp_share["share"] + "/Movie (2024)/movie.mkv"
    config = make_config()
    semaphore = asyncio.Semaphore(2)

    with Session(test_engine) as session:
        session.add(TrackedItem(
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest,
            status="finished", tag="share",
        ))
        session.commit()

    with patch("app.scheduler.copy_file", new_callable=AsyncMock) as mock_copy:
        await _process_item(
            config=config, semaphore=semaphore,
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest, tag="share",
        )
        mock_copy.assert_not_called()


@pytest.mark.asyncio
async def test_copy_failure_sets_error_status(test_engine, tmp_share):
    """When copy_file raises, the item's status must be set to 'error'."""
    src = tmp_share["sample_file"]
    dest = tmp_share["share"] + "/Movie (2024)/movie.mkv"
    config = make_config()
    semaphore = asyncio.Semaphore(2)

    with patch("app.scheduler.copy_file", side_effect=OSError("disk full")), \
         patch("app.scheduler.check_quota", return_value=True):
        await _process_item(
            config=config, semaphore=semaphore,
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest, tag="share",
        )

    with Session(test_engine) as session:
        item = session.exec(select(TrackedItem)).first()
    assert item.status == "error"
    assert "disk full" in item.error_message


# ---------------------------------------------------------------------------
# First-run and approval-gating tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_run_queues_item_as_backlog(test_engine, tmp_share):
    """On first run (first_run_complete=False), item is created as queued+is_backlog; no copy."""
    src = tmp_share["sample_file"]
    dest = tmp_share["share"] + "/Movie (2024)/movie.mkv"
    config = make_config()
    semaphore = asyncio.Semaphore(2)

    with patch("app.scheduler.copy_file", new_callable=AsyncMock) as mock_copy:
        await _process_item(
            config=config, semaphore=semaphore,
            is_first_run=True,  # Passed explicitly — source's first-run flag
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest, tag="share",
        )
        mock_copy.assert_not_called()

    with Session(test_engine) as session:
        item = session.exec(select(TrackedItem)).first()
    assert item.status == "queued"
    assert item.is_backlog is True


@pytest.mark.asyncio
async def test_require_approval_queues_new_item(test_engine, tmp_share):
    """When require_approval=True, a new post-first-run item lands as queued (not copied)."""
    src = tmp_share["sample_file"]
    dest = tmp_share["share"] + "/Movie (2024)/movie.mkv"
    config = make_config(require_approval=True)
    semaphore = asyncio.Semaphore(2)

    with patch("app.scheduler.copy_file", new_callable=AsyncMock) as mock_copy:
        await _process_item(
            config=config, semaphore=semaphore,
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest, tag="share",
        )
        mock_copy.assert_not_called()

    with Session(test_engine) as session:
        item = session.exec(select(TrackedItem)).first()
    assert item.status == "queued"
    assert item.is_backlog is False


@pytest.mark.asyncio
async def test_queued_item_is_not_copied_on_poll(test_engine, tmp_share):
    """An existing queued item must not be copied until approved."""
    src = tmp_share["sample_file"]
    dest = tmp_share["share"] + "/Movie (2024)/movie.mkv"
    config = make_config()
    semaphore = asyncio.Semaphore(2)

    with Session(test_engine) as session:
        session.add(TrackedItem(
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest,
            status="queued", tag="share",
        ))
        session.commit()

    with patch("app.scheduler.copy_file", new_callable=AsyncMock) as mock_copy:
        await _process_item(
            config=config, semaphore=semaphore,
            source="radarr", media_type="movie", source_id=1,
            title="Movie", file_path=src, share_path=dest, tag="share",
        )
        mock_copy.assert_not_called()


@pytest.mark.asyncio
async def test_copied_item_is_skipped_even_if_path_changes(test_engine, tmp_share):
    """A copied item must not be reset even if the source file path changes — the file is still on the share."""
    src_old = tmp_share["sample_file"]
    src_new = tmp_share["share"] + "/movie_new.mkv"
    with open(src_new, "wb") as f:
        f.write(b"upgraded video data")
    dest_old = tmp_share["share"] + "/Movie (2024)/movie.720p.mkv"
    dest_new = tmp_share["share"] + "/Movie (2024)/movie.1080p.mkv"
    config = make_config()
    semaphore = asyncio.Semaphore(2)

    with Session(test_engine) as session:
        session.add(TrackedItem(
            source="radarr", media_type="movie", source_id=10,
            title="MovieUpgraded", file_path=src_old, share_path=dest_old,
            status="copied", tag="share",
        ))
        session.commit()

    with patch("app.scheduler.copy_file", new_callable=AsyncMock) as mock_copy:
        await _process_item(
            config=config, semaphore=semaphore,
            source="radarr", media_type="movie", source_id=10,
            title="MovieUpgraded", file_path=src_new, share_path=dest_new, tag="share",
        )
        mock_copy.assert_not_called()

    with Session(test_engine) as session:
        item = session.exec(select(TrackedItem).where(TrackedItem.source_id == 10)).first()
        # Item must remain untouched
        assert item.status == "copied"
        assert item.is_upgraded is False
        assert item.file_path == src_old
        assert item.share_path == dest_old


@pytest.mark.asyncio
async def test_upgraded_finished_item_resets_to_pending(test_engine, tmp_share):
    """A finished item whose source file path changed should reset to pending and be flagged upgraded."""
    src_old = tmp_share["sample_file"]
    src_new = tmp_share["share"] + "/movie_v2.mkv"
    with open(src_new, "wb") as f:
        f.write(b"v2 video data")
    dest = tmp_share["share"] + "/Movie (2024)/movie.mkv"
    config = make_config()
    semaphore = asyncio.Semaphore(2)

    with Session(test_engine) as session:
        session.add(TrackedItem(
            source="radarr", media_type="movie", source_id=11,
            title="MovieFinishedUpgrade", file_path=src_old, share_path=dest,
            status="finished", tag="share",
        ))
        session.commit()

    with patch("app.scheduler.copy_file", new_callable=AsyncMock), \
         patch("app.scheduler.check_quota", return_value=True):
        await _process_item(
            config=config, semaphore=semaphore,
            source="radarr", media_type="movie", source_id=11,
            title="MovieFinishedUpgrade", file_path=src_new, share_path=dest, tag="share",
        )

    with Session(test_engine) as session:
        item = session.exec(select(TrackedItem).where(TrackedItem.source_id == 11)).first()
        assert item.is_upgraded is True
        assert item.file_path == src_new
