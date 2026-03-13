"""
test_radarr.py — Tests for app/radarr.py (RadarrClient).

Uses pytest-httpx to mock all outbound HTTP calls — never hits a real Radarr instance.
"""

import pytest
import pytest_asyncio
from pytest_httpx import HTTPXMock

from app.radarr import RadarrClient


BASE_URL = "http://radarr.test"
API_KEY = "test-key"

MOVIE_OBJ = {
    "id": 1,
    "title": "Dune: Part Two",
    "year": 2024,
    "tmdbId": 693134,
    "path": "/movies/Dune Part Two (2024)",
}


# ---------------------------------------------------------------------------
# fetch_movies
# ---------------------------------------------------------------------------
class TestFetchMovies:
    async def test_returns_all_movies(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/movie",
            json=[MOVIE_OBJ],
        )
        client = RadarrClient(BASE_URL, API_KEY)
        movies = await client.fetch_movies()
        assert len(movies) == 1
        assert movies[0]["id"] == 1

    async def test_non_2xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/movie",
            status_code=401,
        )
        client = RadarrClient(BASE_URL, API_KEY)
        with pytest.raises(Exception):
            await client.fetch_movies()

    async def test_sends_api_key_header(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/movie",
            json=[],
        )
        client = RadarrClient(BASE_URL, API_KEY)
        await client.fetch_movies()
        request = httpx_mock.get_requests()[0]
        assert request.headers.get("X-Api-Key") == API_KEY


# ---------------------------------------------------------------------------
# update_movie_path
# ---------------------------------------------------------------------------
class TestUpdateMoviePath:
    async def test_sends_put_with_new_path(self, httpx_mock: HTTPXMock):
        new_path = "/movies/Dune - Part Two (2024) {tmdb-693134}"
        updated_obj = {**MOVIE_OBJ, "path": new_path}

        # GET the single movie object
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/movie/1",
            json=MOVIE_OBJ,
        )
        # PUT updated object — note moveFiles=false query param
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/movie/1?moveFiles=false",
            method="PUT",
            json=updated_obj,
        )

        client = RadarrClient(BASE_URL, API_KEY)
        await client.update_movie_path(1, new_path)

        put_request = httpx_mock.get_requests()[1]
        import json
        body = json.loads(put_request.content)
        assert body["path"] == new_path

    async def test_put_non_2xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/movie/1",
            json=MOVIE_OBJ,
        )
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/movie/1?moveFiles=false",
            method="PUT",
            status_code=500,
        )
        client = RadarrClient(BASE_URL, API_KEY)
        with pytest.raises(Exception):
            await client.update_movie_path(1, "/movies/new-path")
