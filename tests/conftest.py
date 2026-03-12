"""
conftest.py — Shared pytest fixtures for Librarian tests.

Provides:
  db_session   — In-memory SQLite session, tables created fresh per test.
  sample_config — AppConfig row seeded with dummy URLs/keys.
  tmp_media    — tmp_path-based library directory with a sample folder pre-created.
  sample_movies — List of dicts mimicking Radarr /api/v3/movie responses.
  sample_series — List of dicts mimicking Sonarr /api/v3/series responses.
"""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import AppConfig, RenameItem, ScanRun


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
@pytest.fixture(name="db_session")
def db_session_fixture():
    """Fresh in-memory SQLite session per test, all tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@pytest.fixture(name="sample_config")
def sample_config_fixture(db_session):
    """
    AppConfig row seeded in the in-memory DB with dummy values.

    Root folders use /movies and /tv so path remapping tests work predictably.
    """
    config = AppConfig(
        id=1,
        radarr_url="http://radarr.test",
        radarr_api_key="radarr-key",
        radarr_root_folder="/movies",
        sonarr_url="http://sonarr.test",
        sonarr_api_key="sonarr-key",
        sonarr_root_folder="/tv",
        batch_size=10,
    )
    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)
    return config


# ---------------------------------------------------------------------------
# Tmp media directory
# ---------------------------------------------------------------------------
@pytest.fixture(name="tmp_media")
def tmp_media_fixture(tmp_path):
    """
    Temporary directory tree that mimics the container /media mount.

    Structure created:
        tmp_path/
          movies/
            Dune.2021.2160p/        ← pre-created folder to rename
          tv/
            Breaking.Bad.S01/       ← pre-created folder to rename

    Returns tmp_path so tests can construct paths as needed.
    """
    (tmp_path / "movies" / "Dune.2021.2160p").mkdir(parents=True)
    (tmp_path / "tv" / "Breaking.Bad.S01").mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Sample Radarr API response data
# ---------------------------------------------------------------------------
@pytest.fixture(name="sample_movies")
def sample_movies_fixture():
    """Two movie objects as returned by Radarr GET /api/v3/movie."""
    return [
        {
            "id": 1,
            "title": "Dune: Part Two",
            "year": 2024,
            "tmdbId": 693134,
            "path": "/movies/Dune Part Two (2024)",
        },
        {
            "id": 2,
            "title": "Interstellar",
            "year": 2014,
            "tmdbId": 157336,
            "path": "/movies/Interstellar (2014) {tmdb-157336}",  # already correct
        },
    ]


# ---------------------------------------------------------------------------
# Sample Sonarr API response data
# ---------------------------------------------------------------------------
@pytest.fixture(name="sample_series")
def sample_series_fixture():
    """Two series objects as returned by Sonarr GET /api/v3/series."""
    return [
        {
            "id": 1,
            "title": "Breaking Bad",
            "year": 2008,
            "tvdbId": 81189,
            "path": "/tv/Breaking.Bad.S01",
        },
        {
            "id": 2,
            "title": "The Wire",
            "year": 2002,
            "tvdbId": 79126,
            "path": "/tv/The Wire (2002) {tvdb-79126}",  # already correct
        },
    ]
