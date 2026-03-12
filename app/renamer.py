"""
renamer.py — Apply logic for Librarian.

Processes approved RenameItems in batches:
  1. Renames the folder on disk (NFS share, mounted read-write in the container).
  2. Updates the arr database path via PUT API.
  3. Updates the RenameItem status to 'done' or 'error'.

Live progress is written to log_buffer so the SSE endpoint can stream it to the UI.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.config import get_radarr_client, get_sonarr_client
from app.log_buffer import log_buffer
from app.models import AppConfig, RenameItem, ScanRun

logger = logging.getLogger(__name__)

# Container-local mount points (fixed by Dockerfile)
MEDIA_MOUNTS = {"radarr": "/media/movies", "sonarr": "/media/tv"}


def remap_to_container(arr_path: str, root_folder: str, media_mount: str) -> str:
    """
    Translate an arr-namespace path to the container-local mount path.

    Example:
        arr_path    = "/movies/Dune.2021.2160p"
        root_folder = "/movies"
        media_mount = "/media/movies"
        → returns    "/media/movies/Dune.2021.2160p"

    Raises ValueError if arr_path does not start with root_folder.
    """
    root = root_folder.rstrip("/")
    path = arr_path.rstrip("/")
    if not path.startswith(root):
        raise ValueError(
            f"Path '{arr_path}' does not start with root folder '{root_folder}'"
        )
    relative = path[len(root):]
    return media_mount.rstrip("/") + relative


async def run_apply(
    scan_run_id: int,
    batch_size: int,
    session: Session,
    config: AppConfig,
) -> None:
    """
    Process all approved RenameItems for a scan run in batches.

    For each item:
      A. Rename folder on disk.
      B. Update arr DB path via PUT (only if A succeeded).
      C. Update item status to 'done' or 'error'.

    A single item error never aborts the batch — processing continues.
    """
    # Load all approved items ordered deterministically
    stmt = select(RenameItem).where(
        RenameItem.scan_run_id == scan_run_id,
        RenameItem.status == "approved",
    )
    items = list(session.exec(stmt).all())

    if not items:
        log_buffer.append("No approved items to process.")
        log_buffer.append("[DONE] Apply complete.")
        return

    # Update ScanRun status
    scan_run = session.get(ScanRun, scan_run_id)
    if scan_run:
        scan_run.status = "applying"
        scan_run.updated_at = datetime.now(UTC)
        session.add(scan_run)
        session.commit()

    root_folder = (
        config.radarr_root_folder
        if items[0].source == "radarr"
        else config.sonarr_root_folder
    )

    log_buffer.append(
        f"Starting apply: {len(items)} items, batch size {batch_size}"
    )

    # Process in batches
    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start : batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        log_buffer.append(
            f"--- Batch {batch_num} ({len(batch)} items) ---"
        )

        for item in batch:
            await _process_item(item, root_folder, session, config)

    # Update ScanRun counters and final status
    _finalise_scan_run(scan_run_id, session)
    log_buffer.append("[DONE] Apply complete.")


async def _process_item(
    item: RenameItem,
    root_folder: str,
    session: Session,
    config: AppConfig,
) -> None:
    """Rename one folder on disk and update the arr database path."""
    source = item.source
    media_mount = MEDIA_MOUNTS[source]

    # --- Step A: Rename on disk ---
    try:
        old_local = remap_to_container(item.current_path, root_folder, media_mount)
        parent_dir = os.path.dirname(old_local)
        new_local = os.path.join(parent_dir, item.expected_folder)
        await asyncio.to_thread(os.rename, old_local, new_local)
        log_buffer.append(f"✔ [{item.title}] disk rename OK")
        logger.info("Renamed on disk: %s → %s", old_local, new_local)
    except Exception as exc:
        msg = f"Disk rename failed: {exc}"
        item.status = "error"
        item.error_message = msg
        item.updated_at = datetime.now(UTC)
        session.add(item)
        session.commit()
        log_buffer.append(f"✘ [{item.title}] disk rename FAILED: {exc}")
        logger.error("Disk rename failed for item %s: %s", item.id, exc)
        return  # do not attempt arr update if disk rename failed

    # --- Step B: Update arr path ---
    try:
        if source == "radarr":
            client = get_radarr_client(config)
            await client.update_movie_path(item.source_id, item.expected_path)
        else:
            client = get_sonarr_client(config)
            await client.update_series_path(item.source_id, item.expected_path)
        item.status = "done"
        log_buffer.append(f"✔ [{item.title}] arr path updated OK")
        logger.info("arr path updated for item %s → %s", item.id, item.expected_path)
    except Exception as exc:
        # Disk was renamed but arr not updated — user must fix manually
        item.status = "error"
        item.error_message = (
            f"Disk renamed to '{item.expected_folder}' but arr update FAILED: {exc}. "
            "Update the path manually in the arr UI."
        )
        log_buffer.append(
            f"⚠ [{item.title}] arr update FAILED — disk was renamed. Manual fix needed."
        )
        logger.error(
            "arr update failed for item %s after disk rename: %s", item.id, exc
        )

    item.updated_at = datetime.now(UTC)
    session.add(item)
    session.commit()


def _finalise_scan_run(scan_run_id: int, session: Session) -> None:
    """Update ScanRun done_count, error_count, and final status."""
    scan_run = session.get(ScanRun, scan_run_id)
    if not scan_run:
        return

    all_items = session.exec(
        select(RenameItem).where(RenameItem.scan_run_id == scan_run_id)
    ).all()

    done_count = sum(1 for i in all_items if i.status == "done")
    error_count = sum(1 for i in all_items if i.status == "error")

    scan_run.done_count = done_count
    scan_run.error_count = error_count
    scan_run.status = "error" if error_count > 0 else "done"
    scan_run.updated_at = datetime.now(UTC)
    session.add(scan_run)
    session.commit()
    log_buffer.append(
        f"Summary: {done_count} done, {error_count} error(s)"
    )
