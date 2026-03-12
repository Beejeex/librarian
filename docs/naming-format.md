# Librarian — Folder Naming Format

## Summary

| Source | Template | Example |
|---|---|---|
| Radarr (movies) | `{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}` | `Dune (2021) {tmdb-438631}` |
| Sonarr (TV) | `{Series TitleYear} {tvdb-{TvdbId}}` | `Breaking Bad (2008) {tvdb-81189}` |

These templates match Radarr's and Sonarr's built-in **folder naming scheme tokens** so the computed names will always agree with what arr itself would generate.

---

## Movie Folder Name (Radarr)

**Template:** `{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}`

### Fields (from Radarr `GET /api/v3/movie`)

| Token | API field | Notes |
|---|---|---|
| `{Movie CleanTitle}` | `title` | Apply `clean_title()` to make filesystem-safe |
| `({Release Year})` | `year` | Integer, e.g. `2021` |
| `{tmdb-{TmdbId}}` | `tmdbId` | Integer TMDB identifier |

### Construction

```python
f"{clean_title(movie['title'])} ({movie['year']}) {{tmdb-{movie['tmdbId']}}}"
```

### Examples

| Raw `title` | `year` | `tmdbId` | Resulting folder name |
|---|---|---|---|
| `Dune` | 2021 | 438631 | `Dune (2021) {tmdb-438631}` |
| `Avengers: Endgame` | 2019 | 299534 | `Avengers - Endgame (2019) {tmdb-299534}` |
| `Spider-Man: No Way Home` | 2021 | 634649 | `Spider-Man - No Way Home (2021) {tmdb-634649}` |
| `What We Do in the Shadows` | 2014 | 257354 | `What We Do in the Shadows (2014) {tmdb-257354}` |

---

## TV Series Folder Name (Sonarr)

**Template:** `{Series TitleYear} {tvdb-{TvdbId}}`

`{Series TitleYear}` expands to `{Series Title} ({Year})`.

Full expansion: `{clean_title} ({year}) {tvdb-{tvdbId}}`

### Fields (from Sonarr `GET /api/v3/series`)

| Token | API field | Notes |
|---|---|---|
| `{Series Title}` | `title` | Apply `clean_title()` |
| `({Year})` | `year` | Series premiere year |
| `{tvdb-{TvdbId}}` | `tvdbId` | Integer TVDB identifier |

### Construction

```python
f"{clean_title(series['title'])} ({series['year']}) {{tvdb-{series['tvdbId']}}}"
```

### Examples

| Raw `title` | `year` | `tvdbId` | Resulting folder name |
|---|---|---|---|
| `Breaking Bad` | 2008 | 81189 | `Breaking Bad (2008) {tvdb-81189}` |
| `The Mandalorian` | 2019 | 361753 | `The Mandalorian (2019) {tvdb-361753}` |
| `S.W.A.T.` | 2017 | 328798 | `S.W.A.T. (2017) {tvdb-328798}` |
| `Hawkeye` | 2021 | 394472 | `Hawkeye (2021) {tvdb-394472}` |

---

## CleanTitle Algorithm

The `clean_title(title: str) -> str` function in `app/naming.py` transforms a raw title into a filesystem-safe string that matches arr's `{Movie CleanTitle}` / `{Series Title}` token output.

### Rules (applied in order)

| Step | Rule | Example |
|---|---|---|
| 1 | Replace `: ` (colon + space) with ` - ` | `Avengers: Endgame` → `Avengers - Endgame` |
| 2 | Replace remaining standalone `:` with `-` | `Part:Two` → `Part-Two` |
| 3 | Remove unsafe chars: `? * " < > \| \ /` | `What?` → `What` |
| 4 | Collapse multiple spaces into one | `Foo  Bar` → `Foo Bar` |
| 5 | Strip leading/trailing spaces and dots | `.Dark.` → `Dark` |

### Characters that ARE preserved

- Hyphens `-`
- Dots `.` (important — `S.W.A.T.` must stay `S.W.A.T.`)
- Apostrophes `'`
- Parentheses `()`
- Ampersands `&`
- Exclamation marks `!`

### Full Reference Table

| Input | Output |
|---|---|
| `Avengers: Endgame` | `Avengers - Endgame` |
| `Spider-Man: No Way Home` | `Spider-Man - No Way Home` |
| `S.W.A.T.` | `S.W.A.T.` |
| `What We Do in the Shadows` | `What We Do in the Shadows` |
| `Don't Look Up` | `Don't Look Up` |
| `Schindler's List` | `Schindler's List` |
| `AC/DC: Let There Be Rock` | `AC-DC - Let There Be Rock` |
| `.Trailing Dot.` | `Trailing Dot` |
| `Double  Space` | `Double Space` |

---

## Important Notes

- Librarian does **not** use Radarr's `cleanTitle` API field. That field is fully lowercase with no spaces (used for searching). Librarian computes its own filesystem-safe clean title.
- The naming templates are **hard-coded** — they are not configurable per-instance. They match the standard arr naming convention that most operators use.
- If arr is configured with a different naming template, the scan will find every item as a mismatch. In that case, Librarian is not the right tool — it assumes the standard template defined above.
