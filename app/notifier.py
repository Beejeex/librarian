"""
Apprise notification integration for Librarian (Tracker).

Sends push notifications via any Apprise-compatible service when key events occur:
  - A file is copied successfully.
  - A copy fails (error).
  - A file is deleted from the share (finished).
  - A first-run index completes and items are ready for approval.

Notifications are disabled when apprise_urls is empty in AppConfig.
All sends are fire-and-forget; failures are logged but never raise.

Configure one or more Apprise notification URLs (newline-separated) in Settings.
Examples: ntfy://ntfy.sh/my-topic, discord://webhook-id/token,
          tgram://bot_token/chat_id, slack://tokenA/tokenB/tokenC/channel
See https://github.com/caronc/apprise for the full list of supported services.
"""

import asyncio
import logging

import apprise

from app.models import AppConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _build_apprise(config: AppConfig) -> apprise.Apprise | None:
    """
    Build an Apprise instance loaded with all configured notification URLs.
    Returns None when no URLs are configured (notifications disabled).
    """
    urls = [u.strip() for u in config.apprise_urls.splitlines() if u.strip()]
    if not urls:
        return None
    apobj = apprise.Apprise()
    for url in urls:
        apobj.add(url)
    return apobj if len(apobj) else None


def send_notification(
    config: AppConfig,
    *,
    title: str,
    message: str,
    notify_type: str = apprise.NotifyType.INFO,
) -> None:
    """
    Send a notification via all configured Apprise URLs (sync).

    Safe to call from watchdog's background thread. Failures are logged at
    WARNING level and swallowed so a notification outage never disrupts the
    main workflow.
    """
    apobj = _build_apprise(config)
    if apobj is None:
        return
    try:
        apobj.notify(title=title, body=message, notify_type=notify_type)
    except Exception as exc:
        logger.warning("Apprise notification failed: %s", exc)


async def send_notification_async(
    config: AppConfig,
    *,
    title: str,
    message: str,
    notify_type: str = apprise.NotifyType.INFO,
) -> None:
    """Async wrapper — runs send_notification() in a thread pool."""
    await asyncio.to_thread(
        send_notification,
        config,
        title=title,
        message=message,
        notify_type=notify_type,
    )


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

async def notify_copied(config: AppConfig, title: str, source: str) -> None:
    """Send a notification when a file has been successfully copied to the share."""
    if not config.notify_on_copied:
        return
    source_label = "Movie" if source == "radarr" else "Episode"
    await send_notification_async(
        config,
        title=f"{source_label} copied",
        message=title,
        notify_type=apprise.NotifyType.SUCCESS,
    )


async def notify_error(config: AppConfig, title: str, source: str, error: str) -> None:
    """Send a notification when a file copy fails."""
    if not config.notify_on_error:
        return
    source_label = "Movie" if source == "radarr" else "Episode"
    await send_notification_async(
        config,
        title=f"{source_label} copy failed",
        message=f"{title}\n\n{error}",
        notify_type=apprise.NotifyType.FAILURE,
    )


async def notify_finished(config: AppConfig, title: str) -> None:
    """Send a notification when a tracked file is deleted from the share (finished)."""
    if not config.notify_on_finished:
        return
    await send_notification_async(
        config,
        title="File finished",
        message=title,
        notify_type=apprise.NotifyType.INFO,
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
    if not config.notify_on_first_run:
        return
    source_label = "Radarr" if source == "radarr" else "Sonarr"
    noun = "item" if count == 1 else "items"
    await send_notification_async(
        config,
        title=f"{source_label} first-run complete",
        message=f"{count} {noun} queued as backlog — approve in Librarian to start copying.",
        notify_type=apprise.NotifyType.SUCCESS,
    )


def notify_finished_sync(config: AppConfig, title: str) -> None:
    """
    Sync version of notify_finished for use from watchdog's non-async thread.

    Identical semantics to notify_finished but callable without an event loop.
    """
    if not config.notify_on_finished:
        return
    send_notification(
        config,
        title="File finished",
        message=title,
        notify_type=apprise.NotifyType.INFO,
    )
