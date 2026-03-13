"""
Unit tests for the Radarr API client (app/radarr.py).

Uses respx to mock HTTP responses — no real Radarr instance needed.
"""

import pytest
import respx
import httpx

from app.radarr import RadarrClient
from tests.conftest import SAMPLE_TAG, SAMPLE_MOVIE


@pytest.mark.asyncio
async def test_resolves_tag_name_to_id(mock_radarr):
    """Tag name 'share' should resolve to ID 42 from the mock tag list."""
    client = RadarrClient("http://radarr:7878", "testkey")
    movies = await client.get_tagged_movies("share")
    assert len(movies) == 1
    assert movies[0].movie_id == 1
    assert movies[0].movie_file_id == 10  # movieFile.id from SAMPLE_MOVIE


@pytest.mark.asyncio
async def test_excludes_movies_without_matching_tag(mock_radarr):
    """Movies that don't carry the configured tag must not be returned."""
    client = RadarrClient("http://radarr:7878", "testkey")
    # Ask for a tag that doesn't exist on any movie
    movies = await client.get_tagged_movies("nonexistent-tag")
    assert movies == []


@pytest.mark.asyncio
async def test_excludes_movies_without_file():
    """Movies with no movieFile should be silently skipped."""
    movie_no_file = {**SAMPLE_MOVIE, "movieFile": None}
    with respx.mock(base_url="http://radarr:7878") as mock:
        mock.get("/api/v3/tag").mock(return_value=httpx.Response(200, json=[SAMPLE_TAG]))
        mock.get("/api/v3/movie").mock(return_value=httpx.Response(200, json=[movie_no_file]))
        client = RadarrClient("http://radarr:7878", "testkey")
        movies = await client.get_tagged_movies("share")
    assert movies == []


@pytest.mark.asyncio
async def test_api_error_returns_empty_list():
    """A non-2xx API response must return an empty list, not raise an exception."""
    with respx.mock(base_url="http://radarr:7878") as mock:
        mock.get("/api/v3/tag").mock(return_value=httpx.Response(500, text="Server Error"))
        client = RadarrClient("http://radarr:7878", "testkey")
        movies = await client.get_tagged_movies("share")
    assert movies == []
