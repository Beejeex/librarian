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

from sqlmodel import Session, select

from app.config import get_radarr_client, get_sonarr_client
from app.models import AppConfig, RenameItem, ScanRun
from app.naming import movie_folder_name, series_folder_name

logger = logging.getLogger(__name__)

# Media mount paths inside the container (fixed by Dockerfile)
MEDIA_MOUNTS = {"radarr": "/media/movies", "sonarr": "/media/tv"}


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
    root_folder = (
        config.radarr_root_folder if source == "radarr" else config.sonarr_root_folder
    )

    for item in items:
        rename_item = _build_rename_item(item, source, scan_run.id, root_folder)
        if rename_item is None:
            continue  # already correct or invalid — skip
        session.add(rename_item)
        mismatch_count += 1

    session.commit()

    # --- Finalise ScanRun ---
    scan_run.total_items = mismatch_count
    scan_run.status = "ready"
    session.add(scan_run)
    session.commit()
    session.refresh(scan_run)

    logger.info(
        "ScanRun %s complete: %s mismatches found for %s",
        scan_run.id,
        mismatch_count,
        source,
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

    # Compute expected name
    if source == "radarr":
        expected_folder = movie_folder_name(item)
    else:
        expected_folder = series_folder_name(item)

    if current_folder == expected_folder:
        return None  # already correct

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
    )
