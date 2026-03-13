"""
Database table definitions for MadTracked.

Defines two tables:
- TrackedItem: one row per media file being tracked (movie or episode).
- AppConfig: single-row table holding all runtime configuration.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class TrackedItem(SQLModel, table=True):
    """Represents a single media file that has been discovered and tracked."""

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
    file_path: str  # Original path on the source media mount
    share_path: str  # Destination path on /share

    # --- State ---
    # status values: queued | pending | copied | finished | error
    # queued:   discovered but not yet approved for copying
    # pending:  approved, waiting for next poll to copy
    # copied:   file successfully on /share
    # finished: file deleted from /share (done)
    # error:    last copy failed; retried automatically on next poll
    status: str = "queued"
    is_backlog: bool = False  # True = discovered during first-run index
    is_upgraded: bool = False  # True = source file changed after item was copied/finished
    file_size_bytes: int = 0  # Recorded at copy time; used for quota accounting
    error_message: Optional[str] = None  # Populated when status is "error"
    tag: str = ""  # Comma-separated names of all tags that matched this item

    # --- Timestamps ---
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AppConfig(SQLModel, table=True):
    """Single-row configuration table. Row ID is always 1."""

    id: Optional[int] = Field(default=1, primary_key=True)

    # --- Radarr ---
    radarr_url: str = ""
    radarr_api_key: str = ""
    radarr_tags: str = ""  # Comma-separated tag names, e.g. "share,watched"

    # --- Sonarr ---
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    sonarr_tags: str = ""  # Comma-separated tag names

    # --- Behaviour ---
    poll_interval_minutes: int = 15
    share_path: str = "/share"
    copy_mode: str = "copy"  # "copy" or "hardlink"
    # Per-source first-run flags: set True after the index-only poll for that source.
    # Only meaningful when the source has tags configured. Never set via env.
    radarr_first_run_complete: bool = False
    sonarr_first_run_complete: bool = False
    require_approval: bool = False  # When True, post-first-run items also land as queued
    max_concurrent_copies: int = 2  # asyncio.Semaphore width per poll run
    max_share_size_gb: float = 0.0  # Total cap in GB; 0 = unlimited. Backlog 60%, New 40%
    max_share_files: int = 0  # Total file count cap; 0 = unlimited. Same 60/40 split

    # --- Media path roots (as seen by Radarr/Sonarr on their host) ---
    # The container maps /media/movies → radarr_root_folder and /media/tv → sonarr_root_folder
    radarr_root_folder: str = "/movies"
    sonarr_root_folder: str = "/tv"
    # --- ntfy.sh notifications ---
    ntfy_url: str = "https://ntfy.sh"  # Base URL; override for self-hosted instances
    ntfy_topic: str = ""  # Topic to publish to; empty = notifications disabled
    ntfy_token: str = ""  # Optional Bearer token for private topics

    # Per-event toggles — only meaningful when ntfy_topic is set
    ntfy_on_copied: bool = True      # Notify when a file is successfully copied
    ntfy_on_error: bool = True       # Notify when a copy fails
    ntfy_on_finished: bool = True    # Notify when a file is deleted from the share
    ntfy_on_first_run: bool = True   # Notify when a first-run index completes