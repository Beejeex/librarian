"""
test_api.py — Tests for app/routers/api.py.

Uses FastAPI's TestClient to test HTTP endpoints with an in-memory SQLite DB.
Verifies:
- GET /health returns {"status": "ok"} with HTTP 200.
- POST /api/items/{id}/approve changes status to 'approved'.
- POST /api/items/{id}/skip changes status to 'skipped'.
- POST /api/items/approve-all approves all pending items.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.main import app
from app.database import get_session_dep as get_session
from app.models import AppConfig, RenameItem, ScanRun


# ---------------------------------------------------------------------------
# Override DB dependency to use in-memory SQLite
# ---------------------------------------------------------------------------
@pytest.fixture(name="client")
def client_fixture():
    """
    TestClient with dependency override pointing at in-memory SQLite.
    Tables are created fresh per test.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="seeded_client")
def seeded_client_fixture():
    """TestClient with a seeded ScanRun + two RenameItems (pending)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    item1_id: int
    item2_id: int
    run_id: int

    with Session(engine) as seed_session:
        config = AppConfig(id=1, radarr_url="http://r.test", radarr_api_key="k",
                           sonarr_url="http://s.test", sonarr_api_key="k")
        seed_session.add(config)

        run = ScanRun(source="radarr", status="ready", total_items=2, done_count=0, error_count=0)
        seed_session.add(run)
        seed_session.commit()
        seed_session.refresh(run)
        run_id = run.id  # captured inside session

        item1 = RenameItem(
            scan_run_id=run_id, source="radarr", source_id=1,
            title="Movie A", current_folder="Old A", expected_folder="New A",
            current_path="/movies/Old A", expected_path="/movies/New A",
            status="pending",
        )
        item2 = RenameItem(
            scan_run_id=run_id, source="radarr", source_id=2,
            title="Movie B", current_folder="Old B", expected_folder="New B",
            current_path="/movies/Old B", expected_path="/movies/New B",
            status="pending",
        )
        seed_session.add(item1)
        seed_session.add(item2)
        seed_session.commit()
        seed_session.refresh(item1)
        seed_session.refresh(item2)
        item1_id = item1.id  # captured inside session
        item2_id = item2.id  # captured inside session

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    yield client, item1_id, item2_id, run_id
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class TestHealth:
    def test_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Item approval / skip
# ---------------------------------------------------------------------------
class TestApproveItem:
    def test_approve_changes_status(self, seeded_client):
        client, item1_id, item2_id, run_id = seeded_client
        response = client.post(f"/api/items/{item1_id}/approve")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"

    def test_approve_unknown_item_returns_404(self, client):
        response = client.post("/api/items/9999/approve")
        assert response.status_code == 404


class TestSkipItem:
    def test_skip_changes_status(self, seeded_client):
        client, item1_id, item2_id, run_id = seeded_client
        response = client.post(f"/api/items/{item1_id}/skip")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"

    def test_skip_unknown_item_returns_404(self, client):
        response = client.post("/api/items/9999/skip")
        assert response.status_code == 404


class TestApproveAll:
    def test_approves_all_pending_items(self, seeded_client):
        client, item1_id, item2_id, run_id = seeded_client
        response = client.post(
            "/api/items/approve-all",
            json={"scan_run_id": run_id},
        )
        assert response.status_code == 200
        assert response.json()["approved"] == 2

    def test_no_items_returns_zero(self, client):
        response = client.post(
            "/api/items/approve-all",
            json={"scan_run_id": 9999},
        )
        assert response.status_code == 200
        assert response.json()["approved"] == 0
