"""
scanner.py — Scan logic for Librarian.

Fetches all movies or series from the arr API, computes the expected folder name
for each item using naming.py, and writes RenameItem rows for any mismatch.
Items already matching the expected name are ignored.

Re-scanning clears all pending/approved/skipped items for the source and
rebuilds the list fresh from the current arr state.
"""

import logging
import os
import re

from sqlmodel import Session, select

from app.config import get_radarr_client, get_sonarr_client
from app.models import AppConfig, RenameItem, ScanRun
from app.naming import movie_folder_name, series_folder_name
from app.renamer import MEDIA_MOUNTS, remap_to_container

logger = logging.getLogger(__name__)


async def run_scan(source: str, session: Session, config: AppConfig) -> ScanRun:
    """
    Scan Radarr or Sonarr for folder name mismatches and record them in the DB.

    Steps:
      1. Create a new ScanRun record.
      2. Clear previous non-done items for this source.
      3. Fetch all items from arr.
      4. Compare current folder name to expected; write RenameItem for mismatches.
      5. Update ScanRun with totals and set status to 'ready'.

    Returns the completed ScanRun.
    """
    # --- Create ScanRun ---
    scan_run = ScanRun(source=source, status="scanning")
    session.add(scan_run)
    session.commit()
    session.refresh(scan_run)
    logger.info("ScanRun %s started for source=%s", scan_run.id, source)

    # --- Clear previous non-done items ---
    _clear_previous_items(source, session)

    # --- Fetch items from arr ---
    try:
        if source == "radarr":
            client = get_radarr_client(config)
            items = await client.fetch_movies()
        else:
            client = get_sonarr_client(config)
            items = await client.fetch_series()
    except Exception as exc:
        logger.error("Failed to fetch items from %s: %s", source, exc)
        scan_run.status = "error"
        session.add(scan_run)
        session.commit()
        return scan_run

    # --- Compare and record mismatches ---
    mismatch_count = 0
    total_found = 0
    batch_size = config.batch_size
    root_folder = (
        config.radarr_root_folder if source == "radarr" else config.sonarr_root_folder
    )
    folder_format = (
        config.radarr_folder_format if source == "radarr" else config.sonarr_folder_format
    )

    for item in items:
        rename_item = _build_rename_item(item, source, scan_run.id, root_folder, folder_format)
        if rename_item is None:
            continue  # already correct or invalid — skip
        total_found += 1
        if mismatch_count < batch_size:
            session.add(rename_item)
            mismatch_count += 1

    session.commit()

    # --- Finalise ScanRun ---
    scan_run.total_items = total_found
    scan_run.status = "ready"
    session.add(scan_run)
    session.commit()
    session.refresh(scan_run)

    logger.info(
        "ScanRun %s complete: %s mismatches found for %s (batch: %s)",
        scan_run.id,
        total_found,
        source,
        mismatch_count,
    )
    return scan_run


def _clear_previous_items(source: str, session: Session) -> None:
    """
    Delete all RenameItems for this source that are not yet done/error.
    Leaves 'done' and 'error' items intact (they belong to completed runs).
    """
    stmt = select(RenameItem).where(
        RenameItem.source == source,
        RenameItem.status.in_(["pending", "approved", "skipped"]),
    )
    items = session.exec(stmt).all()
    for item in items:
        session.delete(item)
    session.commit()
    logger.debug("Cleared %s stale items for source=%s", len(items), source)


def _build_rename_item(
    item: dict,
    source: str,
    scan_run_id: int,
    root_folder: str,
    folder_format: str,
) -> RenameItem | None:
    """
    Build a RenameItem for a single arr item if renaming is needed.

    Returns None if:
      - The path field is empty/missing.
      - The ID fields (tmdbId / tvdbId) are missing or zero.
      - The current folder name already matches the expected name.
    """
    # Validate required ID fields
    id_field = "tmdbId" if source == "radarr" else "tvdbId"
    if not item.get(id_field):
        logger.warning(
            "Skipping %r (id=%s): missing %s", item.get("title"), item.get("id"), id_field
        )
        return None

    current_path = item.get("path", "").rstrip("/")
    if not current_path:
        logger.warning("Skipping %r (id=%s): empty path", item.get("title"), item.get("id"))
        return None

    current_folder = os.path.basename(current_path)

    # Compute expected name using the configured folder format
    if source == "radarr":
        expected_folder = movie_folder_name(item, folder_format)
    else:
        expected_folder = series_folder_name(item, folder_format)

    media_mount = MEDIA_MOUNTS.get(source, "")

    if current_folder == expected_folder:
        # Name matches arr's record — but check disk existence too.
        # Catches: arr was already updated by a previous apply but disk rename never ran.
        if media_mount:
            try:
                local_path = remap_to_container(current_path, root_folder, media_mount)
                if not os.path.isdir(local_path):
                    # Arr path is correct but disk folder is missing.
                    # Try to find the old folder by stripping the ID suffix
                    # (e.g. "Movie (2021)" from "Movie (2021) {tmdb-12345}").
                    parent = os.path.dirname(local_path)
                    base_name = re.sub(
                        r"\s*\{(?:tmdb|tvdb|imdb)-[^}]+\}\s*$", "", expected_folder
                    ).strip()
                    old_disk_local = os.path.join(parent, base_name)
                    if base_name != expected_folder and os.path.isdir(old_disk_local):
                        # Found the old folder — arr is done, disk needs the rename.
                        # current_folder = actual disk name; current_path = arr path (correct).
                        old_disk_path = os.path.join(
                            os.path.dirname(current_path.rstrip("/")), base_name
                        )
                        logger.info(
                            "disk_only: arr correct, disk has old name %r for %r",
                            base_name, item.get("title"),
                        )
                        return RenameItem(
                            scan_run_id=scan_run_id,
                            source=source,
                            source_id=item["id"],
                            title=item["title"],
                            current_folder=base_name,       # actual disk folder name
                            expected_folder=expected_folder,
                            current_path=old_disk_path,     # arr-namespace of actual disk loc
                            expected_path=current_path,     # arr already at expected path
                            status="pending",
                            disk_scenario="disk_only",
                        )
                    else:
                        # Disk folder truly not found anywhere — surface as missing.
                        logger.warning(
                            "Arr path correct but folder missing on disk: %s", current_path
                        )
                        return RenameItem(
                            scan_run_id=scan_run_id,
                            source=source,
                            source_id=item["id"],
                            title=item["title"],
                            current_folder=current_folder,
                            expected_folder=expected_folder,
                            current_path=current_path,
                            expected_path=current_path,
                            status="pending",
                            disk_scenario="missing",
                        )
            except (ValueError, OSError):
                pass
        return None  # arr and disk both correct

    # --- Classify disk scenario ---
    # Check whether the old and new folder paths actually exist on disk
    # so the Review page can show the operator exactly what will happen.
    disk_scenario = "unknown"
    if media_mount:
        try:
            old_local = remap_to_container(current_path, root_folder, media_mount)
            new_local = os.path.join(os.path.dirname(old_local), expected_folder)
            old_exists = os.path.isdir(old_local)
            new_exists = os.path.isdir(new_local)
            if old_exists and not new_exists:
                disk_scenario = "rename"      # normal: rename on disk + update arr
            elif not old_exists and new_exists:
                disk_scenario = "arr_only"    # disk ahead: just update arr path
            elif old_exists and new_exists:
                disk_scenario = "collision"   # both exist: cannot rename safely
            else:
                disk_scenario = "missing"     # neither exists on disk
        except (ValueError, OSError):
            disk_scenario = "unknown"

    # Build the expected full path in arr namespace
    expected_path = os.path.join(root_folder, expected_folder)

    return RenameItem(
        scan_run_id=scan_run_id,
        source=source,
        source_id=item["id"],
        title=item["title"],
        current_folder=current_folder,
        expected_folder=expected_folder,
        current_path=current_path,
        expected_path=expected_path,
        status="pending",
        disk_scenario=disk_scenario,
    )
