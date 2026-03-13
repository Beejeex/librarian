"""
ntfy.sh notification integration for Librarian (Tracker).

Sends push notifications to an ntfy topic when key events occur:
  - A file is copied successfully.
  - A copy fails (error).
  - A file is deleted from the share (finished).
  - A first-run index completes and items are ready for approval.

Notifications are disabled when ntfy_topic is empty in AppConfig.
All sends are fire-and-forget; failures are logged but never raise.

The public API is a single sync function send_notification() (safe to call
from watchdog's background thread) plus async convenience wrappers for use
inside the scheduler's async context via asyncio.to_thread().
"""

import asyncio
import logging
from typing import Literal

import httpx

from app.models import AppConfig

logger = logging.getLogger(__name__)

Priority = Literal["min", "low", "default", "high", "urgent"]


# ---------------------------------------------------------------------------
# Core send
# ---------------------------------------------------------------------------

def send_notification(
    config: AppConfig,
    *,
    title: str,
    message: str,
    priority: Priority = "default",
    tags: list[str] | None = None,
) -> None:
    """
    POST a notification to the configured ntfy topic.

    Does nothing if ntfy_topic is empty. Failures are logged at WARNING level
    and swallowed so a notification outage never disrupts the main workflow.

    Args:
        config:   Current AppConfig, read for ntfy_url / ntfy_topic / ntfy_token.
        title:    Notification title (shown in the ntfy app header).
        message:  Notification body text.
        priority: ntfy priority level (default is "default").
        tags:     Optional list of ntfy emoji shortcodes, e.g. ["white_check_mark"].
    """
    if not config.ntfy_topic:
        return  # Notifications disabled

    url = f"{config.ntfy_url.rstrip('/')}/{config.ntfy_topic}"
    headers: dict[str, str] = {
        "Title": title,
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    if config.ntfy_token:
        headers["Authorization"] = f"Bearer {config.ntfy_token}"

    try:
        response = httpx.post(url, content=message.encode(), headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("ntfy notification failed (%s): %s", url, exc)


async def send_notification_async(
    config: AppConfig,
    *,
    title: str,
    message: str,
    priority: Priority = "default",
    tags: list[str] | None = None,
) -> None:
    """Async wrapper — runs send_notification() in a thread pool."""
    await asyncio.to_thread(
        send_notification,
        config,
        title=title,
        message=message,
        priority=priority,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

async def notify_copied(config: AppConfig, title: str, source: str) -> None:
    """Send a notification when a file has been successfully copied to the share."""
    if not config.ntfy_on_copied:
        return
    source_label = "Movie" if source == "radarr" else "Episode"
    await send_notification_async(
        config,
        title=f"{source_label} copied",
        message=title,
        priority="default",
        tags=["white_check_mark"],
    )


async def notify_error(config: AppConfig, title: str, source: str, error: str) -> None:
    """Send a notification when a file copy fails."""
    if not config.ntfy_on_error:
        return
    source_label = "Movie" if source == "radarr" else "Episode"
    await send_notification_async(
        config,
        title=f"{source_label} copy failed",
        message=f"{title}\n\n{error}",
        priority="high",
        tags=["x"],
    )


async def notify_finished(config: AppConfig, title: str) -> None:
    """Send a notification when a tracked file is deleted from the share (finished)."""
    if not config.ntfy_on_finished:
        return
    await send_notification_async(
        config,
        title="File finished",
        message=title,
        priority="low",
        tags=["wastebasket"],
    )


async def notify_first_run_complete(
    config: AppConfig, source: str, count: int
) -> None:
    """
    Send a notification when a first-run index poll finishes and items are queued.

    Args:
        source: "radarr" or "sonarr".
        count:  Number of items queued as backlog.
    """
    if not config.ntfy_on_first_run:
        return
    source_label = "Radarr" if source == "radarr" else "Sonarr"
    noun = "item" if count == 1 else "items"
    await send_notification_async(
        config,
        title=f"{source_label} first-run complete",
        message=f"{count} {noun} queued as backlog — approve in Librarian to start copying.",
        priority="default",
        tags=["mag"],
    )


def notify_finished_sync(config: AppConfig, title: str) -> None:
    """
    Sync version of notify_finished for use from watchdog's non-async thread.

    Identical semantics to notify_finished but callable without an event loop.
    """
    if not config.ntfy_on_finished:
        return
    send_notification(
        config,
        title="File finished",
        message=title,
        priority="low",
        tags=["wastebasket"],
    )
