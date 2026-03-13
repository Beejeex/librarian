"""
File copy logic for Librarian (Tracker).

All file operations go through this module — never inline shutil calls
in the scheduler or routes. Supports chunked copy with live progress
reporting and automatic subtitle file detection.
Also provides quota helpers used by the scheduler and API endpoints.
"""

import asyncio
import logging
import os
import shutil
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sqlmodel import Session
    from app.models import AppConfig

from app import copy_progress

_COPY_CHUNK = 4 * 1024 * 1024  # 4 MB per read/write cycle

# Subtitle extensions to scan for alongside the main video file
_SUBTITLE_EXTS = {".srt", ".sub", ".ass", ".ssa", ".vtt", ".sup"}

logger = logging.getLogger(__name__)


async def copy_file(
    src: str,
    dest: str,
    mode: str = "copy",
    item_id: Optional[int] = None,
    title: str = "",
) -> None:
    """
    Copy src to dest, creating destination directories as needed.

    Args:
        src:     Absolute path to the source file (on /media).
        dest:    Absolute path to the destination file (on /share).
        mode:    "copy" performs a chunked copy.
        item_id: TrackedItem PK; when provided, progress is reported to
                 copy_progress so the UI can show speed and percentage.
        title:   Human-readable title used in the progress indicator.

    Raises:
        OSError: If the copy operation fails.
    """
    await asyncio.to_thread(_copy_file_sync, src, dest, mode, item_id, title)


def find_subtitle_files(video_path: str) -> list[str]:
    """
    Return a list of subtitle files that accompany the given video file.

    Scans the same directory as the video and returns any file whose stem
    starts with the video stem and whose extension is a known subtitle format.
    For example, alongside Movie.mkv it would find:
      Movie.en.srt, Movie.fr.srt, Movie.srt, Movie.en.ass, etc.

    Returns an empty list if the directory is unreadable or contains no matches.
    """
    src_dir = os.path.dirname(video_path)
    src_stem = os.path.splitext(os.path.basename(video_path))[0]
    results: list[str] = []
    try:
        for entry in os.scandir(src_dir):
            if not entry.is_file():
                continue
            name = entry.name
            ext = os.path.splitext(name)[1].lower()
            if ext not in _SUBTITLE_EXTS:
                continue
            stem = os.path.splitext(name)[0]
            # Match exact stem or stem with a language/qualifier suffix (e.g. .en, .forced)
            if stem == src_stem or stem.startswith(src_stem + "."):
                results.append(entry.path)
    except OSError as exc:
        logger.warning("Could not scan for subtitles in %s: %s", src_dir, exc)
    return results


def _copy_file_sync(
    src: str,
    dest: str,
    mode: str,
    item_id: Optional[int] = None,
    title: str = "",
) -> None:
    """Blocking chunked copy called from a thread pool by copy_file()."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    if mode == "hardlink":
        os.link(src, dest)
        logger.info("Hardlinked %s → %s", src, dest)
        return

    # --- Chunked copy with live progress reporting ---
    total_bytes = os.path.getsize(src)
    filename = os.path.basename(src)

    if item_id is not None:
        copy_progress.start(item_id, title, filename, total_bytes)

    start_ts = time.monotonic()
    bytes_done = 0

    try:
        with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
            while True:
                chunk = fsrc.read(_COPY_CHUNK)
                if not chunk:
                    break
                fdst.write(chunk)
                bytes_done += len(chunk)
                if item_id is not None:
                    elapsed = time.monotonic() - start_ts
                    speed = bytes_done / elapsed if elapsed > 0 else 0
                    copy_progress.update(item_id, bytes_done, speed)
        shutil.copystat(src, dest)  # Preserve source timestamps/permissions
        logger.info("Copied %s → %s", src, dest)

        # Copy any companion subtitle files to the same destination directory
        dest_dir = os.path.dirname(dest)
        for sub_src in find_subtitle_files(src):
            sub_dest = os.path.join(dest_dir, os.path.basename(sub_src))
            try:
                shutil.copy2(sub_src, sub_dest)
                logger.info("Copied subtitle %s → %s", sub_src, sub_dest)
            except OSError as exc:
                # Non-fatal — log and continue; the main file is already copied
                logger.warning("Failed to copy subtitle %s: %s", sub_src, exc)
    finally:
        # Always deregister, even on exception mid-copy
        if item_id is not None:
            copy_progress.finish(item_id)


def build_movie_share_path(share_root: str, title: str, year: int, filename: str) -> str:
    """
    Build the destination path for a movie file on the share.

    Structure: /share/<Title (Year)>/<original filename>

    Args:
        share_root: Root of the share mount, e.g. "/share".
        title:      Movie title as returned by Radarr.
        year:       Release year.
        filename:   Original filename (basename only).
    """
    folder = f"{title} ({year})"
    return os.path.join(share_root, folder, filename)


def build_episode_share_path(
    share_root: str,
    series_title: str,
    season_number: int,
    filename: str,
) -> str:
    """
    Build the destination path for a TV episode file on the share.

    Structure: /share/<Series Title>/Season XX/<original filename>

    Args:
        share_root:    Root of the share mount, e.g. "/share".
        series_title:  Series name as returned by Sonarr.
        season_number: Season number (used to format the season folder).
        filename:      Original filename (basename only).
    """
    season_folder = f"Season {season_number:02d}"
    return os.path.join(share_root, series_title, season_folder, filename)


# ---------------------------------------------------------------------------
# Quota helpers
# ---------------------------------------------------------------------------

def get_file_size(path: str) -> int:
    """
    Return the size of a file in bytes.

    Returns 0 if the file does not exist or cannot be read.
    """
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def get_quota_usage(session: "Session", is_backlog: bool | None = None) -> dict:
    """
    Return current copied-file quota usage.

    Pass is_backlog=True/False to query a specific pool, or is_backlog=None
    to query the combined total across both pools.

    Returns a dict with keys: size_bytes (int), size_gb (float), file_count (int).
    """
    from sqlmodel import select, func
    from app.models import TrackedItem

    conditions = [TrackedItem.status.in_(["copied", "copying"])]
    if is_backlog is not None:
        conditions.append(TrackedItem.is_backlog == is_backlog)

    result = session.exec(
        select(
            func.coalesce(func.sum(TrackedItem.file_size_bytes), 0),
            func.count(),
        ).where(*conditions)
    ).first()

    size_bytes = int(result[0]) if result else 0
    file_count = int(result[1]) if result else 0
    return {
        "size_bytes": size_bytes,
        "size_gb": round(size_bytes / (1024 ** 3), 3),
        "file_count": file_count,
    }


def check_quota(
    session: "Session",
    config: "AppConfig",
    is_backlog: bool,
    prospective_bytes: int = 0,
) -> bool:
    """
    Return True if adding a file of prospective_bytes would still fit within quota.

    Rules:
    - Backlog items are capped at 60% of each limit.
    - New items can use the entire remaining capacity (total cap minus backlog usage).
    - A cap of 0 means unlimited.
    """
    if is_backlog:
        usage = get_quota_usage(session, is_backlog=True)
        if config.max_share_size_gb > 0:
            limit_bytes = int(config.max_share_size_gb * 0.6 * (1024 ** 3))
            if usage["size_bytes"] + prospective_bytes > limit_bytes:
                return False
        if config.max_share_files > 0:
            limit_files = int(config.max_share_files * 0.6)
            if usage["file_count"] + 1 > limit_files:
                return False
    else:
        total_usage = get_quota_usage(session, is_backlog=None)
        if config.max_share_size_gb > 0:
            total_bytes = int(config.max_share_size_gb * (1024 ** 3))
            if total_usage["size_bytes"] + prospective_bytes > total_bytes:
                return False
        if config.max_share_files > 0:
            if total_usage["file_count"] + 1 > config.max_share_files:
                return False

    return True


def get_share_stats(share_path: str) -> dict:
    """
    Walk the share directory and return total file count and cumulative size.

    Returns a dict with keys: size_bytes (int), size_gb (float), file_count (int).
    """
    total_size = 0
    file_count = 0
    for root, _dirs, files in os.walk(share_path):
        for fname in files:
            try:
                total_size += os.path.getsize(os.path.join(root, fname))
                file_count += 1
            except OSError:
                pass
    return {
        "size_bytes": total_size,
        "size_gb": round(total_size / (1024 ** 3), 3),
        "file_count": file_count,
    }
