"""
Shared pytest fixtures for MadTracked tests.

Provides:
- db_session: in-memory SQLite session, fresh tables per test.
- tmp_share: a tmp_path-based share directory with a sample media file.
- mock_radarr / mock_sonarr: respx routers with sample API responses.
"""

import pytest
import respx
import httpx
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

from app.models import TrackedItem, AppConfig


# --- Database fixture ---

@pytest.fixture(name="db_session")
def db_session_fixture():
    """Create an isolated in-memory SQLite DB and return a session. Tables are wiped after each test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


# --- Share directory fixture ---

@pytest.fixture()
def tmp_share(tmp_path):
    """Create a temporary share directory with a sample source media file pre-placed."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    sample_file = media_dir / "Movie.Title.2024.mkv"
    sample_file.write_bytes(b"fake video data")

    share_dir = tmp_path / "share"
    share_dir.mkdir()

    return {
        "media": str(media_dir),
        "share": str(share_dir),
        "sample_file": str(sample_file),
    }


# --- Radarr mock fixture ---

SAMPLE_TAG = {"id": 42, "label": "share"}

SAMPLE_MOVIE = {
    "id": 1,
    "title": "Test Movie",
    "year": 2024,
    "tags": [42],
    "movieFile": {
        "id": 10,
        "path": "/media/Test Movie (2024)/Test.Movie.2024.mkv",
    },
}

SAMPLE_SERIES = {
    "id": 5,
    "title": "Test Show",
    "tags": [42],
}

SAMPLE_EPISODE_FILE = {
    "id": 100,
    "seriesId": 5,
    "seasonNumber": 1,
    "path": "/media/Test Show/Season 01/Test.Show.S01E01.mkv",
}

SAMPLE_EPISODE = [{"episodeNumber": 1, "title": "Pilot"}]


@pytest.fixture()
def mock_radarr():
    """Pre-configured respx router simulating a Radarr v3 API with one tagged movie."""
    # assert_all_called=False: some tests return early (e.g. tag not found) and won't hit every route
    with respx.mock(base_url="http://radarr:7878", assert_all_called=False) as mock:
        mock.get("/api/v3/tag").mock(return_value=httpx.Response(200, json=[SAMPLE_TAG]))
        mock.get("/api/v3/movie").mock(return_value=httpx.Response(200, json=[SAMPLE_MOVIE]))
        yield mock


@pytest.fixture()
def mock_sonarr():
    """Pre-configured respx router simulating a Sonarr v3 API with one tagged series and episode."""
    # assert_all_called=False: some tests return early and won't hit every route
    with respx.mock(base_url="http://sonarr:8989", assert_all_called=False) as mock:
        mock.get("/api/v3/tag").mock(return_value=httpx.Response(200, json=[SAMPLE_TAG]))
        mock.get("/api/v3/series").mock(return_value=httpx.Response(200, json=[SAMPLE_SERIES]))
        mock.get("/api/v3/episodefile").mock(return_value=httpx.Response(200, json=[SAMPLE_EPISODE_FILE]))
        mock.get("/api/v3/episode").mock(return_value=httpx.Response(200, json=SAMPLE_EPISODE))
        yield mock
