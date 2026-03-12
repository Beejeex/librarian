# TODO 09 — Tests & conftest

## Goal
Write all unit and integration tests. The full test suite must pass inside the Docker container with `docker run --rm librarian pytest -v`. No real Radarr/Sonarr connection required; no real NFS share required.

---

## Test Modules

```
tests/
├── conftest.py         ← shared fixtures
├── test_naming.py      ← naming.py
├── test_radarr.py      ← radarr.py
├── test_sonarr.py      ← sonarr.py
├── test_scanner.py     ← scanner.py
├── test_renamer.py     ← renamer.py + log_buffer.py
└── test_api.py         ← routers/api.py health + status endpoints
```

---

## Tasks

### 9.1 — tests/conftest.py

```python
import pytest
from sqlmodel import SQLModel, Session, create_engine

@pytest.fixture(name="session")
def session_fixture():
    """In-memory SQLite session, fresh per test."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

@pytest.fixture
def sample_config(session):
    """AppConfig row seeded with test values."""
    from app.models import AppConfig
    config = AppConfig(
        radarr_url="http://radarr:7878",
        radarr_api_key="testradarrkey",
        radarr_root_folder="/movies",
        sonarr_url="http://sonarr:8989",
        sonarr_api_key="testsonarrkey",
        sonarr_root_folder="/tv",
        batch_size=2,
    )
    session.add(config)
    session.commit()
    return config

@pytest.fixture
def tmp_media(tmp_path):
    """Temporary media directory with a sample folder."""
    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    sample = movies_dir / "Dune.2021.2160p.UHD"
    sample.mkdir()
    return tmp_path

@pytest.fixture
def sample_movies():
    """Three sample Radarr movie API objects (mix of match and mismatch)."""
    return [
        # Has correct name already
        {"id": 1, "title": "Dune", "year": 2021, "tmdbId": 438631,
         "path": "/movies/Dune (2021) {tmdb-438631}"},
        # Needs renaming
        {"id": 2, "title": "Avengers: Endgame", "year": 2019, "tmdbId": 299534,
         "path": "/movies/Avengers.Endgame.2019"},
        # Also needs renaming
        {"id": 3, "title": "The Batman", "year": 2022, "tmdbId": 414906,
         "path": "/movies/Batman.2022.4K"},
    ]

@pytest.fixture
def sample_series():
    """Three sample Sonarr series API objects."""
    return [
        {"id": 10, "title": "Breaking Bad", "year": 2008, "tvdbId": 81189,
         "path": "/tv/Breaking Bad (2008) {tvdb-81189}"},  # already correct
        {"id": 11, "title": "The Mandalorian", "year": 2019, "tvdbId": 361753,
         "path": "/tv/Mandalorian.2019"},
    ]
```

### 9.2 — tests/test_naming.py

See [todo/04-naming-engine.md] for full test table. Implement every row.

```python
import pytest
from app.naming import clean_title, movie_folder_name, series_folder_name

@pytest.mark.parametrize("title,expected", [
    ("Avengers: Endgame", "Avengers - Endgame"),
    ("Spider-Man: No Way Home", "Spider-Man - No Way Home"),
    ("S.W.A.T.", "S.W.A.T."),
    ("What We Do in the Shadows", "What We Do in the Shadows"),
    ("Don't Look Up", "Don't Look Up"),
    ("AC/DC: Let There Be Rock", "AC-DC - Let There Be Rock"),
    (".Leading Dot", "Leading Dot"),
    ("Trailing Dot.", "Trailing Dot"),
    ("Double  Space", "Double Space"),
    ("Part:Two", "Part-Two"),
    ("What?", "What"),
    ("Title*Illegal", "TitleIllegal"),
])
def test_clean_title(title, expected):
    assert clean_title(title) == expected

def test_movie_folder_name():
    movie = {"title": "Dune", "year": 2021, "tmdbId": 438631}
    assert movie_folder_name(movie) == "Dune (2021) {tmdb-438631}"

def test_movie_folder_name_colon_title():
    movie = {"title": "Avengers: Endgame", "year": 2019, "tmdbId": 299534}
    assert movie_folder_name(movie) == "Avengers - Endgame (2019) {tmdb-299534}"

def test_series_folder_name():
    series = {"title": "Breaking Bad", "year": 2008, "tvdbId": 81189}
    assert series_folder_name(series) == "Breaking Bad (2008) {tvdb-81189}"

def test_tmdb_token_is_literal_braces():
    """Ensure the {tmdb-...} token uses literal curly braces, not f-string artefacts."""
    result = movie_folder_name({"title": "Test", "year": 2020, "tmdbId": 12345})
    assert "{tmdb-12345}" in result

def test_tvdb_token_is_literal_braces():
    result = series_folder_name({"title": "Test", "year": 2020, "tvdbId": 99999})
    assert "{tvdb-99999}" in result
```

### 9.3 — tests/test_radarr.py

Use `respx` to mock httpx calls.

```python
import respx, httpx, pytest
from app.radarr import RadarrClient

BASE = "http://radarr:7878"

@pytest.mark.asyncio
async def test_fetch_movies_returns_list():
    with respx.mock:
        respx.get(f"{BASE}/api/v3/movie").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "title": "Dune"}])
        )
        client = RadarrClient(BASE, "key")
        movies = await client.fetch_movies()
    assert len(movies) == 1
    assert movies[0]["title"] == "Dune"

@pytest.mark.asyncio
async def test_fetch_movies_non_2xx_raises():
    with respx.mock:
        respx.get(f"{BASE}/api/v3/movie").mock(return_value=httpx.Response(401))
        client = RadarrClient(BASE, "key")
        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_movies()

@pytest.mark.asyncio
async def test_update_movie_path_puts_full_object():
    movie_obj = {"id": 1, "title": "Dune", "path": "/movies/old", "year": 2021, "tmdbId": 438631}
    with respx.mock:
        respx.get(f"{BASE}/api/v3/movie/1").mock(
            return_value=httpx.Response(200, json=movie_obj)
        )
        put_route = respx.put(f"{BASE}/api/v3/movie/1").mock(
            return_value=httpx.Response(202, json={**movie_obj, "path": "/movies/new"})
        )
        client = RadarrClient(BASE, "key")
        await client.update_movie_path(1, "/movies/new")
    assert put_route.called
    sent_body = put_route.calls[0].request
    import json
    body = json.loads(sent_body.content)
    assert body["path"] == "/movies/new"
    assert body["title"] == "Dune"   # original fields preserved
```

### 9.4 — tests/test_sonarr.py

Same pattern, series endpoints.

### 9.5 — tests/test_scanner.py

See [todo/05-scan-engine.md] for test table. Mock arr clients.

### 9.6 — tests/test_renamer.py

See [todo/06-apply-engine.md] for test table. Use `tmp_media` fixture.

### 9.7 — tests/test_api.py

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_approve_item(session, ...):
    # Create a pending RenameItem in DB, approve it, verify status change
    ...

def test_skip_item(session, ...):
    ...
```

---

## Acceptance Criteria
- [ ] `docker run --rm librarian pytest -v` shows all tests passing
- [ ] No tests call real Radarr/Sonarr — all HTTP is mocked with `respx`
- [ ] No tests touch real filesystem paths — all disk tests use `tmp_path`
- [ ] All `test_naming.py` parametrize rows pass
- [ ] `test_health` passes
- [ ] Scanner, renamer, and api tests pass
