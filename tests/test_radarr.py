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


# ---------------------------------------------------------------------------
# fetch_naming_config
# ---------------------------------------------------------------------------
class TestFetchNamingConfig:
    async def test_returns_folder_and_file_format(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/config/naming",
            json={
                "movieFolderFormat": "{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}",
                "standardMovieFormat": "{Movie CleanTitle} ({Release Year})",
            },
        )
        client = RadarrClient(BASE_URL, API_KEY)
        result = await client.fetch_naming_config()
        assert result["folder_format"] == "{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}"
        assert result["file_format"] == "{Movie CleanTitle} ({Release Year})"

    async def test_missing_keys_return_defaults(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/config/naming",
            json={},
        )
        client = RadarrClient(BASE_URL, API_KEY)
        result = await client.fetch_naming_config()
        assert "folder_format" in result
        assert "file_format" in result
        assert isinstance(result["folder_format"], str)
        assert result["file_format"] == ""


# ---------------------------------------------------------------------------
# fetch_root_folders
# ---------------------------------------------------------------------------
class TestFetchRootFolders:
    async def test_returns_list_of_paths(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/rootfolder",
            json=[{"id": 1, "path": "/movies"}, {"id": 2, "path": "/movies2"}],
        )
        client = RadarrClient(BASE_URL, API_KEY)
        result = await client.fetch_root_folders()
        assert result == ["/movies", "/movies2"]

    async def test_empty_list_returned_on_empty_response(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/rootfolder",
            json=[],
        )
        client = RadarrClient(BASE_URL, API_KEY)
        result = await client.fetch_root_folders()
        assert result == []


# ---------------------------------------------------------------------------
# fetch_file_rename_proposals
# ---------------------------------------------------------------------------
class TestFetchFileRenameProposals:
    async def test_returns_proposal_list(self, httpx_mock: HTTPXMock):
        proposals = [
            {"movieFileId": 10, "existingPath": "/movies/Old.mkv", "newPath": "/movies/New.mkv"},
        ]
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/rename?movieId=1",
            json=proposals,
        )
        client = RadarrClient(BASE_URL, API_KEY)
        result = await client.fetch_file_rename_proposals(1)
        assert len(result) == 1
        assert result[0]["movieFileId"] == 10

    async def test_non_2xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/rename?movieId=1",
            status_code=500,
        )
        client = RadarrClient(BASE_URL, API_KEY)
        with pytest.raises(Exception):
            await client.fetch_file_rename_proposals(1)


# ---------------------------------------------------------------------------
# command_rename_files
# ---------------------------------------------------------------------------
class TestCommandRenameFiles:
    async def test_sends_correct_body(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/command",
            method="POST",
            json={"id": 999},
        )
        client = RadarrClient(BASE_URL, API_KEY)
        await client.command_rename_files(1, [10, 20])

        import json
        request = httpx_mock.get_requests()[0]
        body = json.loads(request.content)
        assert body["name"] == "RenameFiles"
        assert body["movieId"] == 1
        assert body["files"] == [10, 20]

    async def test_non_2xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/command",
            method="POST",
            status_code=500,
        )
        client = RadarrClient(BASE_URL, API_KEY)
        with pytest.raises(Exception):
            await client.command_rename_files(1, [10])
