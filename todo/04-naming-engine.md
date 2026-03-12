# TODO 04 — Naming Engine

## Goal
Implement `app/naming.py` — the single source of truth for computing expected folder names. Every other module that needs a folder name must call these functions; no callers may inline the logic.

---

## Tasks

### 4.1 — clean_title(title: str) -> str

Transforms a raw media title into a filesystem-safe string.

```python
def clean_title(title: str) -> str:
    """
    Make a media title safe for use as a filesystem folder name.
    Matches arr's {Movie CleanTitle} / {Series Title} folder token behaviour.
    """
```

Rules (applied in order):
1. Replace `: ` (colon + space) → ` - `
2. Replace remaining `:` → `-`
3. Remove characters in the set: `? * " < > | \ /`
4. Collapse multiple consecutive spaces → single space
5. Strip leading/trailing spaces
6. Strip leading/trailing dots

### 4.2 — movie_folder_name(movie: dict) -> str

Compute the expected folder name for a Radarr movie object.

Template: `{clean_title} ({year}) {tmdb-{tmdbId}}`

```python
def movie_folder_name(movie: dict) -> str:
    """
    Compute the expected folder name for a Radarr movie.
    Input: raw movie object from GET /api/v3/movie.
    Output: e.g. 'Dune (2021) {tmdb-438631}'
    """
    title = clean_title(movie["title"])
    year = movie["year"]
    tmdb_id = movie["tmdbId"]
    return f"{title} ({year}) {{tmdb-{tmdb_id}}}"
```

Note the double-brace escaping in the f-string: `{{tmdb-{tmdb_id}}}` → produces literal `{tmdb-438631}`.

### 4.3 — series_folder_name(series: dict) -> str

Compute the expected folder name for a Sonarr series object.

Template: `{clean_title} ({year}) {tvdb-{tvdbId}}`

```python
def series_folder_name(series: dict) -> str:
    """
    Compute the expected folder name for a Sonarr series.
    Input: raw series object from GET /api/v3/series.
    Output: e.g. 'Breaking Bad (2008) {tvdb-81189}'
    """
    title = clean_title(series["title"])
    year = series["year"]
    tvdb_id = series["tvdbId"]
    return f"{title} ({year}) {{tvdb-{tvdb_id}}}"
```

---

## Tests — tests/test_naming.py

### clean_title tests

| Input | Expected output |
|---|---|
| `"Avengers: Endgame"` | `"Avengers - Endgame"` |
| `"Spider-Man: No Way Home"` | `"Spider-Man - No Way Home"` |
| `"S.W.A.T."` | `"S.W.A.T."` |
| `"What We Do in the Shadows"` | `"What We Do in the Shadows"` |
| `"Don't Look Up"` | `"Don't Look Up"` |
| `"AC/DC: Let There Be Rock"` | `"AC-DC - Let There Be Rock"` |
| `".Leading Dot"` | `"Leading Dot"` |
| `"Trailing Dot."` | `"Trailing Dot"` |
| `"Double  Space"` | `"Double Space"` |
| `"Part:Two"` | `"Part-Two"` |
| `"What?"` | `"What"` |
| `"Title*Illegal"` | `"TitleIllegal"` |

### movie_folder_name tests

| title | year | tmdbId | Expected |
|---|---|---|---|
| `"Dune"` | 2021 | 438631 | `"Dune (2021) {tmdb-438631}"` |
| `"Avengers: Endgame"` | 2019 | 299534 | `"Avengers - Endgame (2019) {tmdb-299534}"` |

### series_folder_name tests

| title | year | tvdbId | Expected |
|---|---|---|---|
| `"Breaking Bad"` | 2008 | 81189 | `"Breaking Bad (2008) {tvdb-81189}"` |
| `"S.W.A.T."` | 2017 | 328798 | `"S.W.A.T. (2017) {tvdb-328798}"` |

---

## Acceptance Criteria
- [ ] All `test_naming.py` test cases pass
- [ ] `movie_folder_name` and `series_folder_name` use `clean_title()` internally
- [ ] The literal `{tmdb-...}` and `{tvdb-...}` tokens appear verbatim in output (not stripped)
- [ ] No naming logic exists anywhere except `naming.py`
