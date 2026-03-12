"""
naming.py — Folder name computation for Radarr movies and Sonarr series.

This module is the single source of truth for expected folder names.
No other module may inline naming logic — always call these functions.

Default templates match arr's built-in folder naming tokens:
  Movies:  {Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}
  Series:  {Series TitleYear} {tvdb-{TvdbId}}  →  {Title} ({Year}) {tvdb-{TvdbId}}

The format strings are configurable via AppConfig (set in the Settings UI and
fetched automatically when a Test Connection is performed).
"""

import re
from collections.abc import Callable

# ---------------------------------------------------------------------------
# Default format strings (mirror arr's default folder naming templates)
# ---------------------------------------------------------------------------
DEFAULT_MOVIE_FORMAT = "{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}"
DEFAULT_SERIES_FORMAT = "{Series TitleYear} {tvdb-{TvdbId}}"


def clean_title(title: str) -> str:
    """
    Transform a raw media title into a filesystem-safe folder name segment.

    Matches arr's {Movie CleanTitle} / {Series Title} folder token behaviour.
    Rules applied in order:
      1. Replace ': ' (colon + space) with ' - '
      2. Replace remaining ':' with '-'
      3. Remove unsafe characters: ? * " < > | \\ /
      4. Collapse multiple spaces into one
      5. Strip leading/trailing spaces and dots
    """
    # Colon-space → dash-space (most common case: "Title: Subtitle")
    result = title.replace(": ", " - ")
    # Remaining colons (no space): "Part:Two" → "Part-Two"
    result = result.replace(":", "-")
    # Remove characters that are illegal or problematic on common filesystems
    result = re.sub(r'[?*"<>|\\\/]', "", result)
    # Collapse multiple consecutive spaces into one
    result = re.sub(r" {2,}", " ", result)
    # Strip leading/trailing whitespace and dots
    result = result.strip().strip(".")
    return result


# ---------------------------------------------------------------------------
# Token maps — order matters: longer/more-specific tokens listed first so that
# a shorter token that is a visual prefix of a longer one is never matched first.
# ---------------------------------------------------------------------------
_MOVIE_TOKENS: list[tuple[str, Callable[[dict], str]]] = [
    ("{Movie CleanTitle}", lambda m: clean_title(m.get("title", ""))),
    ("{Movie Title}",      lambda m: m.get("title", "")),
    ("{Release Year}",     lambda m: str(m.get("year", ""))),
    # Compound tokens: arr renders these as literal curly-brace IDs in folder names
    ("{tmdb-{TmdbId}}",    lambda m: "{tmdb-" + str(m.get("tmdbId", "")) + "}"),
    ("{imdb-{ImdbId}}",    lambda m: "{imdb-" + str(m.get("imdbId", "")) + "}"),
]

_SERIES_TOKENS: list[tuple[str, Callable[[dict], str]]] = [
    # {Series TitleYear} must precede {Series Title} (shares a prefix substring)
    # Both apply clean_title() — Sonarr's {Series Title} performs filesystem cleaning
    ("{Series TitleYear}",  lambda s: clean_title(s.get("title", "")) + " (" + str(s.get("year", "")) + ")"),
    ("{Series CleanTitle}", lambda s: clean_title(s.get("title", ""))),
    ("{Series Title}",      lambda s: clean_title(s.get("title", ""))),
    ("{Series Year}",       lambda s: str(s.get("year", ""))),
    ("{tvdb-{TvdbId}}",     lambda s: "{tvdb-" + str(s.get("tvdbId", "")) + "}"),
]


def _render(fmt: str, tokens: list[tuple[str, Callable[[dict], str]]], item: dict) -> str:
    """Substitute all known tokens in *fmt* with values from *item*."""
    for token, fn in tokens:
        fmt = fmt.replace(token, fn(item))
    return fmt.strip()


def movie_folder_name(movie: dict, fmt: str = DEFAULT_MOVIE_FORMAT) -> str:
    """
    Compute the expected folder name for a Radarr movie object.

    Input:  raw movie dict from GET /api/v3/movie
    Output: e.g. 'Dune (2021) {tmdb-438631}'
    """
    return _render(fmt, _MOVIE_TOKENS, movie)


def series_folder_name(series: dict, fmt: str = DEFAULT_SERIES_FORMAT) -> str:
    """
    Compute the expected folder name for a Sonarr series object.

    Input:  raw series dict from GET /api/v3/series
    Output: e.g. 'Breaking Bad (2008) {tvdb-81189}'
    """
    return _render(fmt, _SERIES_TOKENS, series)
