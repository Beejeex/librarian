"""
test_copier.py — Tests for copier.py path builders and quota helpers.
"""

import os

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.copier import (
    build_episode_share_path,
    build_movie_share_path,
    check_quota,
    get_quota_usage,
    get_share_stats,
)
from app.models import AppConfig, TrackedItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine_and_session():
    """Create an in-memory SQLite engine + session for quota tests."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_config(**kwargs) -> AppConfig:
    defaults = dict(
        id=1,
        radarr_url="",
        radarr_api_key="",
        sonarr_url="",
        sonarr_api_key="",
        max_share_size_gb=0.0,
        max_share_files=0,
    )
    defaults.update(kwargs)
    return AppConfig(**defaults)


# ---------------------------------------------------------------------------
# build_movie_share_path
# ---------------------------------------------------------------------------

class TestBuildMovieSharePath:
    def test_basic(self):
        result = build_movie_share_path("/share", "Dune Part Two", 2024, "dune.mkv")
        assert result == os.path.join("/share", "Dune Part Two (2024)", "dune.mkv")

    def test_folder_includes_year(self):
        result = build_movie_share_path("/share", "Interstellar", 2014, "file.mkv")
        parts = result.split(os.sep)
        assert "Interstellar (2014)" in parts

    def test_custom_share_root(self):
        result = build_movie_share_path("/mnt/storage", "Film", 2020, "film.mkv")
        assert result.startswith("/mnt/storage")


# ---------------------------------------------------------------------------
# build_episode_share_path
# ---------------------------------------------------------------------------

class TestBuildEpisodeSharePath:
    def test_basic(self):
        result = build_episode_share_path("/share", "Breaking Bad", 1, "s01e01.mkv")
        assert result == os.path.join("/share", "Breaking Bad", "Season 01", "s01e01.mkv")

    def test_season_padding(self):
        result = build_episode_share_path("/share", "Series", 5, "ep.mkv")
        assert "Season 05" in result

    def test_two_digit_season(self):
        result = build_episode_share_path("/share", "Series", 12, "ep.mkv")
        assert "Season 12" in result

    def test_series_folder_name(self):
        result = build_episode_share_path("/share", "The Wire", 2, "file.mkv")
        parts = result.split(os.sep)
        assert "The Wire" in parts
        assert "Season 02" in parts


# ---------------------------------------------------------------------------
# check_quota
# ---------------------------------------------------------------------------

class TestCheckQuota:
    def test_unlimited_always_allowed(self):
        session = _make_engine_and_session()
        config = _make_config(max_share_size_gb=0.0, max_share_files=0)
        assert check_quota(session, config, is_backlog=False, prospective_bytes=10**10)
        assert check_quota(session, config, is_backlog=True, prospective_bytes=10**10)

    def test_file_count_limit_new_item(self):
        session = _make_engine_and_session()
        # Add a copied item so count = 1
        item = TrackedItem(
            source="radarr", media_type="movie", source_id=1,
            title="T", file_path="f", share_path="s",
            status="copied", is_backlog=False, file_size_bytes=1000, tag="",
        )
        session.add(item)
        session.commit()

        # Limit = 1 file; existing count = 1 → adding another should fail
        config = _make_config(max_share_files=1)
        assert not check_quota(session, config, is_backlog=False)

    def test_file_count_allows_when_under_limit(self):
        session = _make_engine_and_session()
        config = _make_config(max_share_files=5)
        # 0 items copied → 0 + 1 = 1 ≤ 5 → allowed
        assert check_quota(session, config, is_backlog=False)

    def test_backlog_uses_60pct_cap(self):
        session = _make_engine_and_session()
        # 3 backlog files copied; limit = 5; 60% of 5 = 3 → adding a 4th is denied
        for i in range(3):
            item = TrackedItem(
                source="radarr", media_type="movie", source_id=i,
                title=f"T{i}", file_path="f", share_path="s",
                status="copied", is_backlog=True, file_size_bytes=100, tag="",
            )
            session.add(item)
        session.commit()

        config = _make_config(max_share_files=5)
        assert not check_quota(session, config, is_backlog=True)

    def test_size_limit_blocks(self):
        session = _make_engine_and_session()
        # 1 copied file of 500 MB; total limit 1 GB
        item = TrackedItem(
            source="radarr", media_type="movie", source_id=1,
            title="T", file_path="f", share_path="s",
            status="copied", is_backlog=False,
            file_size_bytes=500 * 1024 * 1024,  # 500 MB
            tag="",
        )
        session.add(item)
        session.commit()

        config = _make_config(max_share_size_gb=1.0)
        # Trying to add another 600 MB should exceed the 1 GB cap
        assert not check_quota(session, config, is_backlog=False,
                               prospective_bytes=600 * 1024 * 1024)

    def test_size_limit_allows_when_under(self):
        session = _make_engine_and_session()
        config = _make_config(max_share_size_gb=10.0)
        # 0 bytes used → any small file is allowed
        assert check_quota(session, config, is_backlog=False, prospective_bytes=1024)


# ---------------------------------------------------------------------------
# get_share_stats
# ---------------------------------------------------------------------------

class TestGetShareStats:
    def test_empty_directory(self, tmp_path):
        stats = get_share_stats(str(tmp_path))
        assert stats["file_count"] == 0
        assert stats["size_bytes"] == 0

    def test_counts_files(self, tmp_path):
        (tmp_path / "a.mkv").write_bytes(b"x" * 1000)
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.mkv").write_bytes(b"y" * 2000)

        stats = get_share_stats(str(tmp_path))
        assert stats["file_count"] == 2
        assert stats["size_bytes"] == 3000
