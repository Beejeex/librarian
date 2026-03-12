"""
naming.py — Folder name computation for Radarr movies and Sonarr series.

This module is the single source of truth for expected folder names.
No other module may inline naming logic — always call these functions.

Templates:
  Movies:  {Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}
  Series:  {Series TitleYear} {tvdb-{TvdbId}}  →  {Title} ({Year}) {tvdb-{TvdbId}}
"""

import re


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


def movie_folder_name(movie: dict) -> str:
    """
    Compute the expected folder name for a Radarr movie object.

    Input:  raw movie dict from GET /api/v3/movie
    Output: e.g. 'Dune (2021) {tmdb-438631}'
    """
    title = clean_title(movie["title"])
    year = movie["year"]
    tmdb_id = movie["tmdbId"]
    # Double-brace in f-string produces literal curly braces in output
    return f"{title} ({year}) {{tmdb-{tmdb_id}}}"


def series_folder_name(series: dict) -> str:
    """
    Compute the expected folder name for a Sonarr series object.

    Input:  raw series dict from GET /api/v3/series
    Output: e.g. 'Breaking Bad (2008) {tvdb-81189}'
    """
    title = clean_title(series["title"])
    year = series["year"]
    tvdb_id = series["tvdbId"]
    return f"{title} ({year}) {{tvdb-{tvdb_id}}}"
