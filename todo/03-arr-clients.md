# TODO 03 — Arr API Clients

## Goal
Implement the HTTP clients for Radarr and Sonarr using a shared base class. All arr communication lives in these modules — no `httpx` calls outside them.

---

## Tasks

### 3.1 — app/arr_client.py — BaseArrClient

Shared base class for both clients:

```python
class BaseArrClient:
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    async def get(self, path: str) -> dict | list:
        """GET request, return parsed JSON. Raise on non-2xx."""

    async def put(self, path: str, body: dict) -> dict:
        """PUT request with JSON body, return parsed JSON. Raise on non-2xx."""
```

Key behaviours:
- Use `httpx.AsyncClient` (create per-request or per-method, not stored as instance attr to avoid lifecycle issues).
- On non-2xx: log the status code and URL (no API key in log), raise `httpx.HTTPStatusError`.
- `Content-Type: application/json` and `X-Api-Key` on every request.
- Set a reasonable timeout (30 seconds).

### 3.2 — app/radarr.py — RadarrClient

```python
class RadarrClient(BaseArrClient):

    async def fetch_movies(self) -> list[dict]:
        """Fetch all movies from Radarr. Returns list of movie objects."""
        return await self.get("/api/v3/movie")

    async def update_movie_path(self, movie_id: int, new_path: str) -> None:
        """
        Update the folder path for a movie in Radarr's database.
        GETs the full object first, modifies path, PUTs it back.
        Does not trigger a file move.
        """
        movie = await self.get(f"/api/v3/movie/{movie_id}")
        movie["path"] = new_path
        await self.put(f"/api/v3/movie/{movie_id}", movie)
```

### 3.3 — app/sonarr.py — SonarrClient

```python
class SonarrClient(BaseArrClient):

    async def fetch_series(self) -> list[dict]:
        """Fetch all series from Sonarr. Returns list of series objects."""
        return await self.get("/api/v3/series")

    async def update_series_path(self, series_id: int, new_path: str) -> None:
        """
        Update the folder path for a series in Sonarr's database.
        GETs the full object first, modifies path, PUTs it back.
        Does not trigger a file move.
        """
        series = await self.get(f"/api/v3/series/{series_id}")
        series["path"] = new_path
        await self.put(f"/api/v3/series/{series_id}", series)
```

### 3.4 — Client instantiation helpers

In `config.py` or a small factory, provide helpers:
```python
def get_radarr_client(config: AppConfig) -> RadarrClient:
    return RadarrClient(config.radarr_url, config.radarr_api_key)

def get_sonarr_client(config: AppConfig) -> SonarrClient:
    return SonarrClient(config.sonarr_url, config.sonarr_api_key)
```

---

## Tests — tests/test_radarr.py

Use `respx` to mock HTTP responses.

| Test | Description |
|---|---|
| `test_fetch_movies_returns_list` | Mock GET → return 2 movie objects → assert list length 2 |
| `test_fetch_movies_empty` | Mock GET → return `[]` → assert empty list |
| `test_fetch_movies_non_2xx` | Mock GET → return 401 → assert raises / logs error |
| `test_update_movie_path_sends_full_object` | Mock GET + PUT → assert PUT body has modified path and all other fields preserved |
| `test_update_movie_path_put_fails` | Mock GET ok, PUT → 500 → assert raises |

## Tests — tests/test_sonarr.py

Same pattern as Radarr tests but for series endpoints.

---

## Acceptance Criteria
- [ ] `RadarrClient.fetch_movies()` returns a list of movie dicts
- [ ] `SonarrClient.fetch_series()` returns a list of series dicts
- [ ] `update_movie_path` and `update_series_path` do a GET-then-PUT cycle
- [ ] Non-2xx responses are logged (no API key in log) and raise
- [ ] All tests pass
