"""
Integration tests for FastAPI routes (app/routers/api.py and main.py).

Uses the FastAPI TestClient so no running server is needed.
Routes call get_session() directly (not via Depends), so we patch
app.database.engine to point at the test DB for the full request cycle.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.main import app
from app.models import TrackedItem
import app.database as db_module


# --- Override the shared engine for all route tests ---

@pytest.fixture(name="client")
def client_fixture():
    """FastAPI TestClient using an isolated in-memory SQLite DB per test."""
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    # Patch the module-level engine so every get_session() call in routes
    # uses our in-memory test DB instead of /config/madtracked.db
    original_engine = db_module.engine
    db_module.engine = test_engine

    with patch("app.main.start_scheduler"), patch("app.main.start_watcher"), \
         patch("app.main.stop_scheduler"), patch("app.main.stop_watcher"):
        with TestClient(app) as client:
            yield client, Session(test_engine)

    # Restore the original engine after each test
    db_module.engine = original_engine
    SQLModel.metadata.drop_all(test_engine)


def test_health_check(client):
    """GET /health must return 200 with status: ok."""
    test_client, _ = client
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_items_empty(client):
    """GET /api/items returns an empty list when no items are tracked."""
    test_client, _ = client
    response = test_client.get("/api/items")
    assert response.status_code == 200
    assert response.json() == []


def test_get_item_not_found(client):
    """GET /api/items/999 returns 404 for a non-existent item."""
    test_client, _ = client
    response = test_client.get("/api/items/999")
    assert response.status_code == 404


def test_reset_item_to_pending(client):
    """POST /api/items/{id}/reset changes a finished item back to pending."""
    test_client, session = client
    item = TrackedItem(
        source="radarr", media_type="movie", source_id=1,
        title="Test", file_path="/media/a.mkv", share_path="/share/a.mkv",
        status="finished", tag="share",
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    response = test_client.post(f"/api/items/{item.id}/reset")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_reset_error_item_to_pending(client):
    """POST /api/items/{id}/reset also allows resetting error-status items."""
    test_client, session = client
    item = TrackedItem(
        source="radarr", media_type="movie", source_id=2,
        title="Test", file_path="/media/b.mkv", share_path="/share/b.mkv",
        status="error", tag="share",
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    response = test_client.post(f"/api/items/{item.id}/reset")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_reset_pending_item_returns_400(client):
    """POST /api/items/{id}/reset on a pending item must return 400."""
    test_client, session = client
    item = TrackedItem(
        source="radarr", media_type="movie", source_id=3,
        title="Test", file_path="/media/c.mkv", share_path="/share/c.mkv",
        status="pending", tag="share",
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    response = test_client.post(f"/api/items/{item.id}/reset")
    assert response.status_code == 400


def test_approve_queued_item(client):
    """POST /api/items/{id}/approve moves a queued item to pending."""
    test_client, session = client
    item = TrackedItem(
        source="radarr", media_type="movie", source_id=4,
        title="Test", file_path="/media/d.mkv", share_path="/share/d.mkv",
        status="queued", tag="share",
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    response = test_client.post(f"/api/items/{item.id}/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_approve_non_queued_item_returns_400(client):
    """POST /api/items/{id}/approve on a pending item must return 400."""
    test_client, session = client
    item = TrackedItem(
        source="radarr", media_type="movie", source_id=5,
        title="Test", file_path="/media/e.mkv", share_path="/share/e.mkv",
        status="pending", tag="share",
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    response = test_client.post(f"/api/items/{item.id}/approve")
    assert response.status_code == 400


def test_skip_queued_item(client):
    """POST /api/items/{id}/skip marks a queued item as finished without copying."""
    test_client, session = client
    item = TrackedItem(
        source="radarr", media_type="movie", source_id=6,
        title="Test", file_path="/media/f.mkv", share_path="/share/f.mkv",
        status="queued", tag="share",
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    response = test_client.post(f"/api/items/{item.id}/skip")
    assert response.status_code == 200
    assert response.json()["status"] == "finished"


def test_approve_all_items(client):
    """POST /api/items/approve-all moves all queued items to pending."""
    test_client, session = client
    for i in range(3):
        session.add(TrackedItem(
            source="radarr", media_type="movie", source_id=100 + i,
            title=f"Movie {i}", file_path=f"/media/m{i}.mkv", share_path=f"/share/m{i}.mkv",
            status="queued", tag="share",
        ))
    session.commit()

    with patch("app.routers.api.run_poll"):
        response = test_client.post("/api/items/approve-all")
    assert response.status_code == 200
    data = response.json()
    assert data["approved"] == 3

