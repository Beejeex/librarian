"""
Watchdog-based share monitor for MadTracked.

Watches the /share directory for file deletions. When a tracked file
(status=copied) is deleted, its DB record is updated to status=finished
so it is never re-copied on the next poll.
"""

import logging
from datetime import datetime, timezone

from watchdog.events import FileDeletedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import load_config
from app.database import get_session
from app.models import TrackedItem
from app.notifier import notify_finished_sync
from sqlmodel import select

logger = logging.getLogger(__name__)

_observer: Observer | None = None


# --- Observer lifecycle ---

def start_watcher(share_path: str) -> None:
    """Start the watchdog observer thread monitoring share_path."""
    global _observer
    _observer = Observer()
    _observer.schedule(ShareEventHandler(), path=share_path, recursive=True)
    _observer.start()
    logger.info("Watcher started on %s", share_path)


def stop_watcher() -> None:
    """Stop the watchdog observer thread cleanly."""
    global _observer
    if _observer and _observer.is_alive():
        _observer.stop()
        _observer.join()
        logger.info("Watcher stopped.")


# --- Event handler ---

class ShareEventHandler(FileSystemEventHandler):
    """Handles filesystem events on the share and updates the DB on file deletion."""

    def on_deleted(self, event: FileDeletedEvent) -> None:
        """
        Fired when any file or directory is deleted from the share.

        Only acts on files (not directories) that exist in the DB with status=copied.
        """
        if event.is_directory:
            return

        deleted_path = event.src_path
        _mark_finished_if_tracked(deleted_path)


def _mark_finished_if_tracked(share_path: str) -> None:
    """
    Look up the deleted path in the DB and set its status to 'finished' if found.

    Args:
        share_path: The absolute path of the deleted file on the share.
    """
    with get_session() as session:
        item = session.exec(
            select(TrackedItem).where(
                TrackedItem.share_path == share_path,
                TrackedItem.status == "copied",
            )
        ).first()

        if item is None:
            # Deleted file wasn't one we're tracking — nothing to do
            return

        finished_title = ""  # assigned below before commit
        item.status = "finished"
        item.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.commit()
        logger.info("Marked finished (deleted from share): %s", item.title)
        finished_title = item.title

    # Send notification outside the DB session to avoid holding the connection
    try:
        config = load_config()
        notify_finished_sync(config, finished_title)
    except Exception:
        pass  # Never let a notification failure affect the watcher
    return
