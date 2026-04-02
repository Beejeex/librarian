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


def delete_share_item(share_path: str) -> None:
    """
    Delete the entire parent folder of a stale share file using shutil.rmtree.

    Used when an item's share path becomes stale (e.g. source folder was renamed
    by the Renamer after the file was already copied). Deletes the whole folder so
    no leftover files (subtitles, nfo, etc.) remain before the item is re-copied.
    """
    try:
        parent = os.path.dirname(share_path)
        if os.path.isdir(parent):
            shutil.rmtree(parent)
            logger.info("Deleted stale share folder: %s", parent)
        elif os.path.isfile(share_path):
            os.remove(share_path)
            logger.info("Deleted stale share file: %s", share_path)
    except OSError as exc:
        logger.warning("Could not delete stale share folder %s: %s", share_path, exc)


def build_movie_share_path(share_root: str, arr_file_path: str, root_folder: str) -> str:
    """
    Build the destination path for a movie file on the share.

    Derives the folder name directly from the arr-reported file path so the
    share structure always mirrors what Radarr actually has on disk, regardless
    of the naming format configured in settings.

    Structure: /share/<arr folder name>/<original filename>

    Args:
        share_root:    Root of the share mount, e.g. "/share".
        arr_file_path: Absolute file path as reported by Radarr.
        root_folder:   Radarr root folder prefix (e.g. "/movies").
    """
    remaining = arr_file_path.removeprefix(root_folder).lstrip("/")
    parts = remaining.split("/")
    if len(parts) >= 2:
        folder, filename = parts[0], parts[-1]
        return os.path.join(share_root, folder, filename)
    # File sits directly in root_folder (no subfolder) — unlikely but safe
    return os.path.join(share_root, parts[-1])


def build_episode_share_path(share_root: str, arr_file_path: str, root_folder: str) -> str:
    """
    Build the destination path for a TV episode file on the share.

    Derives folder names directly from the arr-reported file path so the
    share structure always mirrors what Sonarr actually has on disk, regardless
    of the naming format configured in settings.

    Structure: /share/<series folder>/<season folder>/<original filename>

    Args:
        share_root:    Root of the share mount, e.g. "/share".
        arr_file_path: Absolute file path as reported by Sonarr.
        root_folder:   Sonarr root folder prefix (e.g. "/tv").
    """
    remaining = arr_file_path.removeprefix(root_folder).lstrip("/")
    parts = remaining.split("/")
    if len(parts) >= 3:
        return os.path.join(share_root, parts[0], parts[1], parts[-1])
    if len(parts) == 2:
        return os.path.join(share_root, parts[0], parts[1])
    return os.path.join(share_root, parts[-1])


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
    - Total cap is a hard limit for every item.
    - Backlog items are also subject to the tighter backlog sub-cap (60% of total).
    - A cap of 0 means unlimited.
    """
    # Hard total cap — applies to all items
    total_usage = get_quota_usage(session, is_backlog=None)
    if config.max_share_size_gb > 0:
        total_bytes = int(config.max_share_size_gb * (1024 ** 3))
        if total_usage["size_bytes"] + prospective_bytes > total_bytes:
            return False
    if config.max_share_files > 0:
        if total_usage["file_count"] + 1 > config.max_share_files:
            return False

    if is_backlog:
        # Backlog sub-cap (60% of total limits)
        usage = get_quota_usage(session, is_backlog=True)
        if config.max_share_size_gb > 0:
            limit_bytes = int(config.max_share_size_gb * 0.6 * (1024 ** 3))
            if usage["size_bytes"] + prospective_bytes > limit_bytes:
                return False
        if config.max_share_files > 0:
            limit_files = int(config.max_share_files * 0.6)
            if usage["file_count"] + 1 > limit_files:
                return False

    return True


def get_share_stats(share_path: str) -> dict:
    """
    Walk the share directory and return total file count and cumulative size.

    Returns a dict with keys: size_bytes (int), size_gb (float), file_count (int),
    disk_free_bytes (int | None), disk_free_gb (float | None).
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
    try:
        free = shutil.disk_usage(share_path).free
    except Exception:
        free = None
    return {
        "size_bytes": total_size,
        "size_gb": round(total_size / (1024 ** 3), 3),
        "file_count": file_count,
        "disk_free_bytes": free,
        "disk_free_gb": round(free / (1024 ** 3), 2) if free is not None else None,
    }
