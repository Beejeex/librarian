"""
models.py — SQLModel table definitions for Librarian.

Three tables:
  - AppConfig   : single-row application configuration (always id=1)
  - ScanRun     : one record per scan invocation
  - RenameItem  : one record per folder mismatch found during a scan
"""

from datetime import UTC, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class AppConfig(SQLModel, table=True):
    """
    Single-row configuration table.
    id is always 1 — there is exactly one config row.
    """

    id: int = Field(default=1, primary_key=True)
    radarr_url: str = ""
    radarr_api_key: str = ""
    radarr_root_folder: str = "/movies"
    radarr_folder_format: str = "{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}"
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    sonarr_root_folder: str = "/tv"
    sonarr_folder_format: str = "{Series TitleYear} {tvdb-{TvdbId}}"
    batch_size: int = 20
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ScanRun(SQLModel, table=True):
    """
    Represents one scan invocation (Radarr or Sonarr).
    Tracks overall progress from scanning through applying.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str  # "radarr" or "sonarr"
    status: str  # "scanning", "ready", "applying", "done", "error"
    total_items: int = 0
    done_count: int = 0
    error_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RenameItem(SQLModel, table=True):
    """
    One row per folder that needs renaming.
    Written during scan, status updated during apply.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    scan_run_id: int = Field(foreign_key="scanrun.id")
    source: str  # "radarr" or "sonarr"
    source_id: int  # arr's internal ID for the movie/series
    title: str  # human-readable display title
    current_folder: str  # basename of current path
    expected_folder: str  # computed target folder name
    current_path: str  # full path in arr namespace (as arr reports it)
    expected_path: str  # full expected path in arr namespace
    status: str = "pending"  # pending, approved, skipped, done, error
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
