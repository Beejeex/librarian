"""
Unit tests for the watchdog share monitor (app/watcher.py).

Tests that file deletions from the share correctly transition DB items
from 'copied' to 'finished', and that untracked deletions are ignored.
"""

import pytest
from unittest.mock import patch
from sqlmodel import select

from app.models import TrackedItem
from app.watcher import _mark_finished_if_tracked


def make_item(session, share_path: str, status: str = "copied") -> TrackedItem:
    """Helper to insert a TrackedItem into the test DB session."""
    item = TrackedItem(
        source="radarr",
        media_type="movie",
        source_id=1,
        title="Test Movie",
        file_path="/media/test.mkv",
        share_path=share_path,
        status=status,
        tag="share",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_deleted_copied_file_is_marked_finished(db_session):
    """Deleting a file that is tracked as 'copied' should set its status to 'finished'."""
    share_path = "/share/Test Movie (2024)/test.mkv"
    make_item(db_session, share_path, status="copied")

    with patch("app.watcher.get_session", return_value=db_session):
        _mark_finished_if_tracked(share_path)

    item = db_session.exec(select(TrackedItem)).first()
    assert item.status == "finished"


def test_untracked_deletion_has_no_side_effects(db_session):
    """Deleting a file that is not in the DB should not raise or modify anything."""
    with patch("app.watcher.get_session", return_value=db_session):
        # Should complete without error even though no matching item exists
        _mark_finished_if_tracked("/share/some/random/file.mkv")

    assert db_session.exec(select(TrackedItem)).first() is None


def test_finished_item_is_not_double_transitioned(db_session):
    """A file already marked 'finished' in the DB should not be re-processed."""
    share_path = "/share/Test Movie (2024)/test.mkv"
    make_item(db_session, share_path, status="finished")

    with patch("app.watcher.get_session", return_value=db_session):
        # Calling with a path whose item is already finished should be a no-op
        _mark_finished_if_tracked(share_path)

    item = db_session.exec(select(TrackedItem)).first()
    # Status must remain 'finished' — not change to something else
    assert item.status == "finished"
