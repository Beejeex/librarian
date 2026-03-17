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


# ---------------------------------------------------------------------------
# fetch_naming_config
# ---------------------------------------------------------------------------
class TestFetchNamingConfig:
    async def test_returns_folder_and_file_format(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/config/naming",
            json={
                "seriesFolderFormat": "{Series TitleYear} {tvdb-{TvdbId}}",
                "standardEpisodeFormat": "{Series Title} - S{season:00}E{episode:00}",
            },
        )
        client = SonarrClient(BASE_URL, API_KEY)
        result = await client.fetch_naming_config()
        assert result["folder_format"] == "{Series TitleYear} {tvdb-{TvdbId}}"
        assert result["file_format"] == "{Series Title} - S{season:00}E{episode:00}"

    async def test_missing_keys_return_defaults(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/config/naming",
            json={},
        )
        client = SonarrClient(BASE_URL, API_KEY)
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
            json=[{"id": 1, "path": "/tv"}, {"id": 2, "path": "/tv2"}],
        )
        client = SonarrClient(BASE_URL, API_KEY)
        result = await client.fetch_root_folders()
        assert result == ["/tv", "/tv2"]

    async def test_empty_list_returned_on_empty_response(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/rootfolder",
            json=[],
        )
        client = SonarrClient(BASE_URL, API_KEY)
        result = await client.fetch_root_folders()
        assert result == []


# ---------------------------------------------------------------------------
# fetch_file_rename_proposals
# ---------------------------------------------------------------------------
class TestFetchFileRenameProposals:
    async def test_returns_proposal_list(self, httpx_mock: HTTPXMock):
        proposals = [
            {"episodeFileId": 5, "existingPath": "/tv/Show/S01E01.old.mkv", "newPath": "/tv/Show/S01E01.mkv"},
        ]
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/rename?seriesId=1",
            json=proposals,
        )
        client = SonarrClient(BASE_URL, API_KEY)
        result = await client.fetch_file_rename_proposals(1)
        assert len(result) == 1
        assert result[0]["episodeFileId"] == 5

    async def test_non_2xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/rename?seriesId=1",
            status_code=500,
        )
        client = SonarrClient(BASE_URL, API_KEY)
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
        client = SonarrClient(BASE_URL, API_KEY)
        await client.command_rename_files(1, [5, 6])

        import json
        request = httpx_mock.get_requests()[0]
        body = json.loads(request.content)
        assert body["name"] == "RenameFiles"
        assert body["seriesId"] == 1
        assert body["files"] == [5, 6]

    async def test_non_2xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/command",
            method="POST",
            status_code=500,
        )
        client = SonarrClient(BASE_URL, API_KEY)
        with pytest.raises(Exception):
            await client.command_rename_files(1, [5])


class TestRefreshSeries:
    async def test_sends_correct_body(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/command",
            method="POST",
            json={"id": 1000},
        )
        client = SonarrClient(BASE_URL, API_KEY)
        await client.refresh_series(7)

        import json
        request = httpx_mock.get_requests()[0]
        body = json.loads(request.content)
        assert body["name"] == "RefreshSeries"
        assert body["seriesId"] == 7

    async def test_non_2xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v3/command",
            method="POST",
            status_code=500,
        )
        client = SonarrClient(BASE_URL, API_KEY)
        with pytest.raises(Exception):
            await client.refresh_series(7)
