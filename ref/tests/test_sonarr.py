"""
Unit tests for the Sonarr API client (app/sonarr.py).

Uses respx to mock HTTP responses — no real Sonarr instance needed.
"""

import pytest
import respx
import httpx

from app.sonarr import SonarrClient
from tests.conftest import SAMPLE_TAG, SAMPLE_SERIES, SAMPLE_EPISODE_FILE, SAMPLE_EPISODE


@pytest.mark.asyncio
async def test_returns_episode_files_for_tagged_series(mock_sonarr):
    """A tagged series with one episode file should produce one SonarrEpisodeFile."""
    client = SonarrClient("http://sonarr:8989", "testkey")
    files = await client.get_tagged_episode_files("share")
    assert len(files) == 1
    assert files[0].series_title == "Test Show"
    assert files[0].season_number == 1
    assert files[0].episode_number == 1


@pytest.mark.asyncio
async def test_excludes_series_without_matching_tag(mock_sonarr):
    """Series that don't carry the configured tag must be excluded."""
    client = SonarrClient("http://sonarr:8989", "testkey")
    files = await client.get_tagged_episode_files("nonexistent-tag")
    assert files == []


@pytest.mark.asyncio
async def test_handles_no_episode_files():
    """A tagged series with no episode files should return an empty list gracefully."""
    with respx.mock(base_url="http://sonarr:8989") as mock:
        mock.get("/api/v3/tag").mock(return_value=httpx.Response(200, json=[SAMPLE_TAG]))
        mock.get("/api/v3/series").mock(return_value=httpx.Response(200, json=[SAMPLE_SERIES]))
        mock.get("/api/v3/episodefile").mock(return_value=httpx.Response(200, json=[]))
        client = SonarrClient("http://sonarr:8989", "testkey")
        files = await client.get_tagged_episode_files("share")
    assert files == []


@pytest.mark.asyncio
async def test_api_error_returns_empty_list():
    """A non-2xx API response must return an empty list, not raise an exception."""
    with respx.mock(base_url="http://sonarr:8989") as mock:
        mock.get("/api/v3/tag").mock(return_value=httpx.Response(401, text="Unauthorized"))
        client = SonarrClient("http://sonarr:8989", "testkey")
        files = await client.get_tagged_episode_files("share")
    assert files == []
