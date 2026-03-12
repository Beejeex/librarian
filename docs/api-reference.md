# Librarian — Radarr & Sonarr API Reference

## Authentication

All requests require the API key in the header:

```
X-Api-Key: <your_api_key>
```

Never pass the API key as a query parameter. Never log the API key.

---

## Radarr API

Base URL: configured as `radarr_url` (e.g. `http://192.168.1.10:7878`)

### GET /api/v3/movie — Fetch all movies

Returns a JSON array of all movies in the Radarr library.

**Request**
```
GET /api/v3/movie
X-Api-Key: APIKEY
```

**Key response fields per movie object**

| Field | Type | Description |
|---|---|---|
| `id` | int | Radarr's internal movie ID |
| `title` | str | Display title, e.g. `"Avengers: Endgame"` |
| `year` | int | Release year |
| `tmdbId` | int | TMDB identifier |
| `path` | str | Current folder path as Radarr sees it, e.g. `/movies/Avengers.Endgame.2019/` |
| `cleanTitle` | str | All-lowercase search-optimised title (do NOT use for folder naming) |

**Example response fragment**
```json
[
  {
    "id": 123,
    "title": "Dune",
    "year": 2021,
    "tmdbId": 438631,
    "path": "/movies/Dune.2021.2160p.UHD",
    ...
  }
]
```

---

### PUT /api/v3/movie/{id} — Update movie path

Updates the folder path stored in Radarr's database. Does **not** move any files — Radarr only updates its DB record.

**Workflow: always GET first, modify, PUT back**
```
1. GET /api/v3/movie/{id}          → full movie object
2. Modify object['path']           → new folder path
3. PUT /api/v3/movie/{id}          → send full object back
```

Never construct a partial PUT body — Radarr requires many required fields. Always round-trip the full object.

**Request**
```
PUT /api/v3/movie/123
X-Api-Key: APIKEY
Content-Type: application/json

{
  "id": 123,
  "title": "Dune",
  "year": 2021,
  "tmdbId": 438631,
  "path": "/movies/Dune (2021) {tmdb-438631}",
  ... (all other fields unchanged)
}
```

**Result**
- ✔ Radarr database path updated
- ✔ No file move
- ✔ No `moveFiles` parameter needed (default is false)

**Success response:** HTTP 202 Accepted, returns updated movie object.

---

## Sonarr API

Base URL: configured as `sonarr_url` (e.g. `http://192.168.1.10:8989`)

### GET /api/v3/series — Fetch all series

Returns a JSON array of all series in the Sonarr library.

**Request**
```
GET /api/v3/series
X-Api-Key: APIKEY
```

**Key response fields per series object**

| Field | Type | Description |
|---|---|---|
| `id` | int | Sonarr's internal series ID |
| `title` | str | Display title, e.g. `"Breaking Bad"` |
| `year` | int | Series premiere year |
| `tvdbId` | int | TheTVDB identifier |
| `path` | str | Current series folder path as Sonarr sees it |
| `cleanTitle` | str | All-lowercase search title (do NOT use for folder naming) |

**Example response fragment**
```json
[
  {
    "id": 321,
    "title": "Breaking Bad",
    "year": 2008,
    "tvdbId": 81189,
    "path": "/tv/Breaking.Bad.2008",
    ...
  }
]
```

---

### PUT /api/v3/series/{id} — Update series path

Updates the series folder path in Sonarr's database. Does **not** move any files.

**Workflow: always GET first, modify, PUT back**
```
1. GET /api/v3/series/{id}         → full series object
2. Modify object['path']           → new folder path
3. PUT /api/v3/series/{id}         → send full object back
```

**Request**
```
PUT /api/v3/series/321
X-Api-Key: APIKEY
Content-Type: application/json

{
  "id": 321,
  "title": "Breaking Bad",
  "year": 2008,
  "tvdbId": 81189,
  "path": "/tv/Breaking Bad (2008) {tvdb-81189}",
  ... (all other fields unchanged)
}
```

**Result**
- ✔ Sonarr database path updated
- ✔ No file move
- ✔ No `moveFiles` parameter needed

**Success response:** HTTP 202 Accepted, returns updated series object.

---

## Path Conventions

### The `path` field

For both Radarr movies and Sonarr series, the `path` field is the **folder path**, not a file path.

- Radarr: `/movies/Dune (2021) {tmdb-438631}` ← no trailing slash, no filename
- Sonarr: `/tv/Breaking Bad (2008) {tvdb-81189}` ← no trailing slash

### Path remapping (arr namespace → container namespace)

Radarr/Sonarr run on a different host (or see paths via their own volume mounts). Their `path` values use their configured root folder prefix. Librarian's container has the same NFS share mounted at a different path.

**Formula:**
```
container_path = arr_path.replace(root_folder, media_mount, 1)
```

**Examples:**
```
arr path:       /movies/Dune.2021.2160p
root_folder:    /movies
media_mount:    /media/movies
container path: /media/movies/Dune.2021.2160p

arr path:       /tv/Breaking.Bad
root_folder:    /tv
media_mount:    /media/tv
container path: /media/tv/Breaking.Bad
```

Both `root_folder` and `media_mount` are fixed:
- `root_folder` = configurable (default `/movies` or `/tv`)
- `media_mount` = always `/media/movies` or `/media/tv` (fixed by Dockerfile)

---

## Error Handling

| Condition | Action |
|---|---|
| Network error | Log warning, mark item `error`, continue with next |
| HTTP 4xx (bad request, not found) | Log error with status code, mark item `error` |
| HTTP 5xx (server error) | Log error with status code, mark item `error` |
| Empty response body | Log warning, treat as error |

API keys must **never** appear in log lines. Strip or mask them before logging request details.

---

## Notes on arr API Versions

Librarian targets the **v3** API which is the stable API for:
- Radarr v3.x and v4.x
- Sonarr v3.x and v4.x

There is no v2 fallback. The `/api/v3/` prefix is part of every endpoint URL.
