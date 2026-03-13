"""
models.py — SQLModel table definitions for Librarian.

Four tables:
  - AppConfig   : single-row application configuration (always id=1)
  - ScanRun     : one record per scan invocation (Renamer)
  - RenameItem  : one record per folder mismatch found during a scan (Renamer)
  - TrackedItem : one record per media file being tracked (Tracker)
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

    # --- Renamer: Radarr ---
    radarr_url: str = ""
    radarr_api_key: str = ""
    radarr_root_folder: str = "/movies"
    radarr_folder_format: str = "{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}"

    # --- Renamer: Sonarr ---
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    sonarr_root_folder: str = "/tv"
    sonarr_folder_format: str = "{Series TitleYear} {tvdb-{TvdbId}}"

    # --- Renamer ---
    batch_size: int = 20

    # --- Tracker: tags ---
    radarr_tags: str = ""  # Comma-separated tag names to watch in Radarr
    sonarr_tags: str = ""  # Comma-separated tag names to watch in Sonarr

    # --- Tracker: behaviour ---
    poll_interval_minutes: int = 15
    share_path: str = "/share"
    copy_mode: str = "copy"  # always "copy"; hardlink removed from UI
    radarr_first_run_complete: bool = False  # set after first index-only Radarr poll
    sonarr_first_run_complete: bool = False  # set after first index-only Sonarr poll
    require_approval: bool = False  # when True, post-first-run items land as queued
    max_concurrent_copies: int = 2  # asyncio.Semaphore width per poll run
    max_share_size_gb: float = 0.0  # total cap in GB; 0 = unlimited
    max_share_files: int = 0  # total file count cap; 0 = unlimited

    # --- Notifications (ntfy.sh) ---
    ntfy_url: str = "https://ntfy.sh"  # base URL; override for self-hosted
    ntfy_topic: str = ""  # topic to publish to; empty = disabled
    ntfy_token: str = ""  # optional Bearer token for private topics
    ntfy_on_copied: bool = True
    ntfy_on_error: bool = True
    ntfy_on_finished: bool = True
    ntfy_on_first_run: bool = True

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
    disk_scenario: str = "unknown"  # rename | arr_only | collision | missing | unknown
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TrackedItem(SQLModel, table=True):
    """
    One row per media file tracked by the Tracker tool.

    Movies are one row per movie file.
    TV episodes are one row per episode file.
    """

    id: Optional[int] = Field(default=None, primary_key=True)

    # --- Source identification ---
    source: str  # "radarr" or "sonarr"
    media_type: str  # "movie" or "episode"
    source_id: int  # Movie ID (Radarr) or episode file ID (Sonarr)
    series_id: Optional[int] = None  # Sonarr series ID; null for movies

    # --- Display info ---
    title: str  # Movie title or "Series S01E02"
    series_title: Optional[str] = None  # Series name (Sonarr only)
    season_number: Optional[int] = None
    episode_number: Optional[int] = None

    # --- File paths ---
    file_path: str  # Original path on the source media mount (/media/...)
    share_path: str  # Destination path on /share

    # --- State ---
    # queued:   discovered but not yet approved
    # pending:  approved, waiting for next poll to copy
    # copied:   file successfully on /share
    # finished: file deleted from /share (done)
    # error:    last copy failed; retried automatically on next poll
    status: str = "queued"
    is_backlog: bool = False  # True = discovered during first-run index
    is_upgraded: bool = False  # True = source file path changed after item was finished
    file_size_bytes: int = 0  # Recorded at copy time; used for quota accounting
    error_message: Optional[str] = None
    tag: str = ""  # Comma-separated names of all tags that matched this item

    # --- Timestamps ---
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
