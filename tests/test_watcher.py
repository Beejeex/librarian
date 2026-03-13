"""
test_watcher.py — Tests for watcher.py share-deletion logic.

Tests _mark_finished_if_tracked which updates a TrackedItem to finished when
its share file is deleted.
"""

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import TrackedItem
from app.watcher import _mark_finished_if_tracked


# ---------------------------------------------------------------------------
# In-memory DB wired to the module under test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_get_session(monkeypatch, db_session):
    """
    Replace app.watcher.get_session with our in-memory db_session.

    _mark_finished_if_tracked uses 'with get_session() as session:' so we
    provide a context manager that yields the fixture's session.
    """
    from contextlib import contextmanager

    @contextmanager
    def _fake_session():
        yield db_session

    monkeypatch.setattr("app.watcher.get_session", _fake_session)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMarkFinishedIfTracked:
    SHARE_PATH = "/share/Test Movie (2024)/movie.mkv"

    def _insert_item(self, db_session, status: str = "copied") -> TrackedItem:
        item = TrackedItem(
            source="radarr",
            media_type="movie",
            source_id=1,
            title="Test Movie (2024)",
            file_path="/media/movies/Test Movie (2024)/movie.mkv",
            share_path=self.SHARE_PATH,
            status=status,
            is_backlog=False,
            is_upgraded=False,
            file_size_bytes=1_000_000,
            tag="seed",
        )
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)
        return item

    def test_marks_copied_item_as_finished(self, db_session):
        item = self._insert_item(db_session, status="copied")
        _mark_finished_if_tracked(self.SHARE_PATH)
        db_session.refresh(item)
        assert item.status == "finished"

    def test_ignores_pending_item(self, db_session):
        """Items that haven't been copied yet should not be marked finished."""
        item = self._insert_item(db_session, status="pending")
        _mark_finished_if_tracked(self.SHARE_PATH)
        db_session.refresh(item)
        assert item.status == "pending"

    def test_ignores_untracked_path(self, db_session):
        """Deleting a path we don't track should not affect anything."""
        item = self._insert_item(db_session, status="copied")
        _mark_finished_if_tracked("/share/some/other/file.mkv")
        db_session.refresh(item)
        assert item.status == "copied"  # unchanged

    def test_already_finished_item_not_touched(self, db_session):
        """Deleting a file for an already-finished item should not raise errors."""
        item = self._insert_item(db_session, status="finished")
        # Should complete without error; item remains finished
        _mark_finished_if_tracked(self.SHARE_PATH)
        db_session.refresh(item)
        assert item.status == "finished"
