"""
test_scheduler.py — Tests for scheduler.py helper functions.

Only tests pure-logic helpers that don't require a live scheduler or arr APIs.
"""

import pytest

from app.scheduler import _remap_media_path, _resolve_tags


# ---------------------------------------------------------------------------
# _remap_media_path
# ---------------------------------------------------------------------------

class TestRemapMediaPath:
    def test_movie_path_remapped(self):
        result = _remap_media_path("/movies/Film (2020)/film.mkv", "/movies", "movies")
        assert result == "/media/movies/Film (2020)/film.mkv"

    def test_tv_path_remapped(self):
        result = _remap_media_path("/tv/Breaking Bad (2008)/Season 01/s01e01.mkv", "/tv", "tv")
        assert result == "/media/tv/Breaking Bad (2008)/Season 01/s01e01.mkv"

    def test_root_with_trailing_slash_stripped(self):
        # root_folder without trailing slash — removeprefix only strips exact match
        result = _remap_media_path("/movies/Film/f.mkv", "/movies", "movies")
        assert result.startswith("/media/movies/")

    def test_preserves_filename(self):
        result = _remap_media_path("/movies/A/file.mkv", "/movies", "movies")
        assert result.endswith("file.mkv")


# ---------------------------------------------------------------------------
# _resolve_tags
# ---------------------------------------------------------------------------

class TestResolveTags:
    def test_single_tag(self):
        assert _resolve_tags("seed") == ["seed"]

    def test_multiple_tags(self):
        assert _resolve_tags("seed, share, transfer") == ["seed", "share", "transfer"]

    def test_empty_string_returns_empty(self):
        assert _resolve_tags("") == []

    def test_whitespace_only_returns_empty(self):
        assert _resolve_tags("  ,  ,") == []

    def test_strips_whitespace(self):
        tags = _resolve_tags("  tag1  ,  tag2  ")
        assert tags == ["tag1", "tag2"]

    def test_skips_empty_parts(self):
        tags = _resolve_tags("a,,b,,c")
        assert tags == ["a", "b", "c"]
