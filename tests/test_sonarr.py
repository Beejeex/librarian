"""
test_sonarr.py — Tests for app/sonarr.py (SonarrClient).

Uses pytest-httpx to mock all outbound HTTP calls — never hits a real Sonarr instance.
"""

import pytest
from pytest_httpx import HTTPXMock

from app.sonarr import SonarrClient


BASE_URL = "http://sonarr.test"
API_KEY = "test-key"

SERIES_OBJ = {
    "id": 1,
    "title": "Breaking Bad",
    "year": 2008,
    "tvdbId": 81189,
    "path": "/tv/Breaking.Bad.S01",
}


# ---------------------------------------------------------------------------
# fetch_series
# ---------------------------------------------------------------------------
class TestFetchSeries:
    async def test_returns_all_series(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/series",
            json=[SERIES_OBJ],
        )
        client = SonarrClient(BASE_URL, API_KEY)
        series = await client.fetch_series()
        assert len(series) == 1
        assert series[0]["id"] == 1

    async def test_non_2xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/series",
            status_code=403,
        )
        client = SonarrClient(BASE_URL, API_KEY)
        with pytest.raises(Exception):
            await client.fetch_series()

    async def test_sends_api_key_header(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/series",
            json=[],
        )
        client = SonarrClient(BASE_URL, API_KEY)
        await client.fetch_series()
        request = httpx_mock.get_requests()[0]
        assert request.headers.get("X-Api-Key") == API_KEY


# ---------------------------------------------------------------------------
# update_series_path
# ---------------------------------------------------------------------------
class TestUpdateSeriesPath:
    async def test_sends_put_with_new_path(self, httpx_mock: HTTPXMock):
        new_path = "/tv/Breaking Bad (2008) {tvdb-81189}"
        updated_obj = {**SERIES_OBJ, "path": new_path}

        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/series/1",
            json=SERIES_OBJ,
        )
        # PUT updated object — note moveFiles=false query param
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/series/1?moveFiles=false",
            method="PUT",
            json=updated_obj,
        )

        client = SonarrClient(BASE_URL, API_KEY)
        await client.update_series_path(1, new_path)

        put_request = httpx_mock.get_requests()[1]
        import json
        body = json.loads(put_request.content)
        assert body["path"] == new_path

    async def test_put_non_2xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/series/1",
            json=SERIES_OBJ,
        )
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/series/1?moveFiles=false",
            method="PUT",
            status_code=500,
        )
        client = SonarrClient(BASE_URL, API_KEY)
        with pytest.raises(Exception):
            await client.update_series_path(1, "/tv/new-path")
