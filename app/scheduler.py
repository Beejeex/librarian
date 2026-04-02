"""
APScheduler-based poll loop for Librarian (Tracker).

Polls Radarr and Sonarr at a configurable interval, discovers newly tagged
files, and copies them to the share. Supports:
- First-run index-only mode: on first poll all items are queued as backlog,
  nothing is copied. The user reviews and approves via the UI.
- Approval-gated mode: when require_approval=True, post-first-run items also
  land as 'queued' and must be approved before they are copied.
- Quota enforcement: backlog items share 60% of the configured caps; new items
  share 40%. A quota hit leaves the item as 'pending' to retry next poll.
- Semaphore: max_concurrent_copies limits parallel file copies per poll cycle.

An asyncio lock prevents overlapping poll runs if a cycle takes longer than
the configured interval.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import load_config, save_config
from app.copier import (
    build_episode_share_path,
    build_movie_share_path,
    check_quota,
    cleanup_failed_copy,
    copy_file,
    delete_share_item,
    get_file_size,
    get_quota_usage,
)
from app.database import get_session
from app.models import AppConfig, TrackedItem
from app.notifier import notify_copied, notify_error, notify_first_run_complete
from app.radarr import RadarrClient
from app.sonarr import SonarrClient
from sqlmodel import select, func

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()
_poll_lock = asyncio.Lock()  # Prevents concurrent poll runs


def is_poll_running() -> bool:
    """Return True if a poll cycle is currently executing."""
    return _poll_lock.locked()


def get_next_poll_time() -> datetime | None:
    """Return the next scheduled poll run time (UTC), or None if scheduler is not running."""
    if not _scheduler.running:
        return None
    job = _scheduler.get_job("poll_job")
    return job.next_run_time if job else None


def _remap_media_path(file_path: str, root_folder: str, subfolder: str) -> str:
    """
    Translate an absolute path from Radarr/Sonarr into its container equivalent.

    Radarr/Sonarr report paths as they exist on their own host (e.g. /movies/Film/file.mkv).
    Inside the container those files are mounted under /media/<subfolder>/.
    Example: root_folder=/movies, subfolder=movies
      /movies/Film/file.mkv  →  /media/movies/Film/file.mkv
    """
    remainder = file_path.removeprefix(root_folder)
    return f"/media/{subfolder}{remainder}"


def _resolve_tags(raw_tags: str) -> list[str]:
    """Split a comma-separated tag string into a cleaned list of non-empty tag names."""
    return [t.strip() for t in raw_tags.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def start_scheduler(config: AppConfig) -> None:
    """Start the APScheduler with the poll interval from config."""
    _scheduler.add_job(
        run_poll,
        trigger="interval",
        minutes=config.poll_interval_minutes,
        id="poll_job",
        next_run_time=datetime.now(),  # Run immediately on startup
    )
    _scheduler.start()
    logger.info("Scheduler started. Poll interval: %d min.", config.poll_interval_minutes)


def stop_scheduler() -> None:
    """Shut down the scheduler gracefully, waiting for any running job to finish."""
    if _scheduler.running:
        _scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped.")


def reschedule_poll(minutes: int) -> None:
    """
    Update the poll job interval without restarting the scheduler.

    Called after the user saves a new poll_interval_minutes in the Config UI.
    Has no effect if the scheduler is not running.
    """
    if _scheduler.running:
        _scheduler.reschedule_job(
            "poll_job",
            trigger="interval",
            minutes=minutes,
        )
        logger.info("Poll interval rescheduled to %d minutes.", minutes)


# ---------------------------------------------------------------------------
# Poll entry point
# ---------------------------------------------------------------------------

def _reconcile_share() -> None:
    """
    Mark 'copied' items as 'finished' if their share file no longer exists.

    Runs at the start of each poll as a reliable fallback for watchdog, which
    may miss delete events on Docker Windows bind-mounts (FUSE/VirtioFS).
    """
    with get_session() as session:
        items = session.exec(
            select(TrackedItem).where(TrackedItem.status == "copied")
        ).all()
        for item in items:
            if not os.path.exists(item.share_path):
                item.status = "finished"
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                logger.info(
                    "Reconcile: '%s' no longer on share — marked finished.", item.title
                )
        session.commit()


async def run_poll() -> None:
    """
    Entry point fired by the scheduler on each interval tick.

    Skips the run if a previous poll is still in progress to avoid
    concurrent DB writes and duplicate file copies.
    """
    if _poll_lock.locked():
        logger.warning("Poll already in progress — skipping this tick.")
        return

    async with _poll_lock:
        try:
            config = load_config()
            semaphore = asyncio.Semaphore(max(1, config.max_concurrent_copies))

            # Reconcile share filesystem against DB before polling sources
            await asyncio.to_thread(_reconcile_share)

            # A source's first-run only triggers when it has tags configured
            # and its flag hasn't been set yet.
            radarr_first_run = (
                not config.radarr_first_run_complete
                and bool(config.radarr_tags.strip())
            )
            sonarr_first_run = (
                not config.sonarr_first_run_complete
                and bool(config.sonarr_tags.strip())
            )

            await _poll_radarr(config, semaphore, radarr_first_run, copy_enabled=False)
            await _poll_sonarr(config, semaphore, sonarr_first_run, copy_enabled=False)

            # Copy phase — non-backlog items first so new arrivals have priority
            if not (radarr_first_run or sonarr_first_run):
                await _copy_pending_items(config, semaphore)
            # Flip per-source flags after their index-only run
            config_changed = False
            if radarr_first_run:
                config.radarr_first_run_complete = True
                config_changed = True
                logger.info(
                    "Radarr first-run index complete — items queued as backlog. "
                    "Approve via the UI to start copying."
                )
                await notify_first_run_complete(
                    config, "radarr", _count_new_backlog("radarr")
                )
            if sonarr_first_run:
                config.sonarr_first_run_complete = True
                config_changed = True
                logger.info(
                    "Sonarr first-run index complete — items queued as backlog. "
                    "Approve via the UI to start copying."
                )
                await notify_first_run_complete(
                    config, "sonarr", _count_new_backlog("sonarr")
                )
            if config_changed:
                save_config(config)
        except Exception:
            # Catch-all so a bug in poll logic never kills the scheduler
            logger.exception("Unexpected error during poll cycle.")


# ---------------------------------------------------------------------------
# Per-source poll helpers
# ---------------------------------------------------------------------------

async def _poll_radarr(
    config: AppConfig,
    semaphore: asyncio.Semaphore,
    is_first_run: bool = False,
    copy_enabled: bool = True,
) -> None:
    """Fetch movies tagged with any configured Radarr tag and process each."""
    if not config.radarr_url or not config.radarr_api_key:
        logger.debug("Radarr not configured — skipping.")
        return

    tag_names = _resolve_tags(config.radarr_tags)
    if not tag_names:
        logger.debug("No Radarr tags configured — skipping.")
        return

    client = RadarrClient(config.radarr_url, config.radarr_api_key)

    # Collect all tagged movies; de-duplicate by file ID, union-merge matched tags
    seen: dict[int, dict] = {}  # movie_file_id → {movie, tags: set}
    for tag_name in tag_names:
        try:
            movies = await client.get_tagged_movies(tag_name)
        except Exception:
            logger.exception("Failed to fetch Radarr movies for tag %r.", tag_name)
            continue
        for movie in movies:
            fid = movie.movie_file_id
            if fid not in seen:
                seen[fid] = {"movie": movie, "tags": set()}
            seen[fid]["tags"].add(tag_name)

    tasks = []
    for entry in seen.values():
        movie = entry["movie"]
        matched_tags = ",".join(sorted(entry["tags"]))
        share_path = build_movie_share_path(
            config.share_path, movie.file_path, config.radarr_root_folder
        )
        container_path = _remap_media_path(
            movie.file_path, config.radarr_root_folder, "movies"
        )
        tasks.append(
            _process_item(
                config=config,
                semaphore=semaphore,
                is_first_run=is_first_run,
                copy_enabled=copy_enabled,
                source="radarr",
                media_type="movie",
                source_id=movie.movie_file_id,
                title=movie.title,
                file_path=container_path,
                share_path=share_path,
                tag=matched_tags,
            )
        )

    await asyncio.gather(*tasks)

    # Orphan detection: any radarr item in an active state whose source_id was
    # not seen in this poll no longer exists in Radarr (file replaced/removed).
    # Mark it with an informative error so it shows up clearly in the UI.
    if seen:  # only run when we actually fetched something successfully
        seen_ids = set(seen.keys())
        with get_session() as session:
            orphans = session.exec(
                select(TrackedItem).where(
                    TrackedItem.source == "radarr",
                    TrackedItem.status.in_(["queued", "pending", "error"]),
                    ~TrackedItem.source_id.in_(seen_ids),
                )
            ).all()
            for orphan in orphans:
                orphan.status = "error"
                orphan.error_message = (
                    "Source file no longer tagged in Radarr — "
                    "the file may have been replaced or untagged. "
                    "Use Delete to remove this item."
                )
                orphan.updated_at = datetime.now(timezone.utc)
                session.add(orphan)
                logger.warning(
                    "Orphaned radarr item %d ('%s') — source_id %d not in current poll.",
                    orphan.id, orphan.title, orphan.source_id,
                )
            if orphans:
                session.commit()


async def _poll_sonarr(
    config: AppConfig,
    semaphore: asyncio.Semaphore,
    is_first_run: bool = False,
    copy_enabled: bool = True,
) -> None:
    """Fetch episode files for series tagged with any configured Sonarr tag and process each."""
    if not config.sonarr_url or not config.sonarr_api_key:
        logger.debug("Sonarr not configured — skipping.")
        return

    tag_names = _resolve_tags(config.sonarr_tags)
    if not tag_names:
        logger.debug("No Sonarr tags configured — skipping.")
        return

    client = SonarrClient(config.sonarr_url, config.sonarr_api_key)

    # Collect all tagged episode files; de-duplicate by file ID, union-merge matched tags
    seen: dict[int, dict] = {}  # episode_file_id → {ef, tags: set}
    for tag_name in tag_names:
        try:
            episode_files = await client.get_tagged_episode_files(tag_name)
        except Exception:
            logger.exception("Failed to fetch Sonarr files for tag %r.", tag_name)
            continue
        for ef in episode_files:
            fid = ef.episode_file_id
            if fid not in seen:
                seen[fid] = {"ef": ef, "tags": set()}
            seen[fid]["tags"].add(tag_name)

    tasks = []
    for entry in seen.values():
        ef = entry["ef"]
        matched_tags = ",".join(sorted(entry["tags"]))
        share_path = build_episode_share_path(
            config.share_path, ef.file_path, config.sonarr_root_folder
        )
        container_path = _remap_media_path(
            ef.file_path, config.sonarr_root_folder, "tv"
        )
        tasks.append(
            _process_item(
                config=config,
                semaphore=semaphore,
                is_first_run=is_first_run,
                copy_enabled=copy_enabled,
                source="sonarr",
                media_type="episode",
                source_id=ef.episode_file_id,
                title=ef.title,
                file_path=container_path,
                share_path=share_path,
                tag=matched_tags,
                series_id=ef.series_id,
                series_title=ef.series_title,
                season_number=ef.season_number,
                episode_number=ef.episode_number,
            )
        )

    await asyncio.gather(*tasks)

    # Orphan detection: any sonarr item in an active state whose source_id was
    # not seen in this poll no longer exists in Sonarr (file replaced/removed).
    if seen:
        seen_ids = set(seen.keys())
        with get_session() as session:
            orphans = session.exec(
                select(TrackedItem).where(
                    TrackedItem.source == "sonarr",
                    TrackedItem.status.in_(["queued", "pending", "error"]),
                    ~TrackedItem.source_id.in_(seen_ids),
                )
            ).all()
            for orphan in orphans:
                orphan.status = "error"
                orphan.error_message = (
                    "Source file no longer tagged in Sonarr — "
                    "the file may have been replaced or untagged. "
                    "Use Delete to remove this item."
                )
                orphan.updated_at = datetime.now(timezone.utc)
                session.add(orphan)
                logger.warning(
                    "Orphaned sonarr item %d ('%s') — source_id %d not in current poll.",
                    orphan.id, orphan.title, orphan.source_id,
                )
            if orphans:
                session.commit()


# ---------------------------------------------------------------------------
# Core per-item logic
# ---------------------------------------------------------------------------

async def _process_item(
    *,
    config: AppConfig,
    semaphore: asyncio.Semaphore,
    is_first_run: bool = False,
    copy_enabled: bool = True,
    source: str,
    media_type: str,
    source_id: int,
    title: str,
    file_path: str,
    share_path: str,
    tag: str,
    series_id: Optional[int] = None,
    series_title: Optional[str] = None,
    season_number: Optional[int] = None,
    episode_number: Optional[int] = None,
) -> None:
    """
    Record and (conditionally) copy a single media file.

    First-run mode (is_first_run=True, passed by _poll_radarr / _poll_sonarr):
      - Creates the item as status='queued', is_backlog=True.
      - Does not copy anything — user must approve via the UI.

    Normal mode (is_first_run=False):
      - require_approval=True  → item lands as 'queued'; user must approve.
      - require_approval=False → item lands as 'pending' and is copied this poll.

    copy_enabled=False: upsert only — copying is deferred to _copy_pending_items.

    Items already in the DB:
      - finished or copied → skip (never re-copy).
      - queued             → update tag if changed; do not copy yet.
      - pending or error   → attempt copy.
    """

    # Read source file size up-front so it can be stored at item creation time
    # (visible in the UI for queued/pending items) and reused for quota projection.
    src_size = await asyncio.to_thread(get_file_size, file_path)

    # --- Upsert logic ---
    with get_session() as session:
        existing = session.exec(
            select(TrackedItem).where(
                TrackedItem.source == source,
                TrackedItem.source_id == source_id,
            )
        ).first()

        if existing:
            if existing.status == "copied":
                # Check if the source path or computed share path has changed
                # (e.g. folder was renamed by Renamer after the file was already copied).
                path_changed = existing.file_path != file_path
                share_stale = existing.share_path != share_path
                if path_changed or share_stale:
                    # Delete the stale copy from the share before re-queueing
                    old_share_path = existing.share_path
                    existing.file_path = file_path
                    existing.share_path = share_path
                    existing.file_size_bytes = src_size
                    existing.is_upgraded = path_changed
                    existing.status = "pending"
                    existing.error_message = None
                    existing.updated_at = datetime.now(timezone.utc)
                    session.add(existing)
                    session.commit()
                    await asyncio.to_thread(delete_share_item, old_share_path)
                    logger.info(
                        "Path changed for copied item [%s]: '%s' — deleted old share copy, resetting to pending.",
                        source, title,
                    )
                    item = existing
                else:
                    # File is still on the share at the correct path — leave it alone
                    logger.debug("Skipping copied item: %s", title)
                    return
            if existing.status == "finished":
                # Reset if source file changed (upgrade) or share_path is stale
                # (e.g. path-building logic was corrected in a newer release).
                path_changed = existing.file_path != file_path
                share_stale = existing.share_path != share_path
                if path_changed or share_stale:
                    existing.file_path = file_path
                    existing.share_path = share_path
                    existing.file_size_bytes = src_size
                    existing.is_upgraded = path_changed  # only a true upgrade if source changed
                    existing.status = "pending"
                    existing.error_message = None
                    existing.updated_at = datetime.now(timezone.utc)
                    session.add(existing)
                    session.commit()
                    if path_changed:
                        logger.info(
                            "Upgrade detected [%s]: '%s' — file path changed, resetting to pending.",
                            source, title,
                        )
                    else:
                        logger.info(
                            "Share path corrected [%s]: '%s' — resetting to pending for re-copy.",
                            source, title,
                        )
                    item = existing
                else:
                    logger.debug("Skipping finished item: %s", title)
                    return
            elif existing.status == "queued":
                # Waiting for approval — update paths and tag/size if changed
                changed = False
                if existing.file_path != file_path:
                    existing.file_path = file_path
                    changed = True
                if existing.share_path != share_path:
                    existing.share_path = share_path
                    changed = True
                if existing.tag != tag:
                    existing.tag = tag
                    changed = True
                if existing.file_size_bytes != src_size and src_size > 0:
                    existing.file_size_bytes = src_size
                    changed = True
                if changed:
                    existing.updated_at = datetime.now(timezone.utc)
                    session.add(existing)
                    session.commit()
                return
            else:
                # pending or error → fall through to copy; refresh paths and size if changed
                changed = False
                if existing.file_path != file_path:
                    existing.file_path = file_path
                    changed = True
                if existing.share_path != share_path:
                    existing.share_path = share_path
                    changed = True
                if existing.file_size_bytes != src_size and src_size > 0:
                    existing.file_size_bytes = src_size
                    changed = True
                if changed:
                    existing.updated_at = datetime.now(timezone.utc)
                    session.add(existing)
                    session.commit()
                item = existing
        else:
            initial_status = "queued" if (is_first_run or config.require_approval) else "pending"
            item = TrackedItem(
                source=source,
                media_type=media_type,
                source_id=source_id,
                title=title,
                file_path=file_path,
                share_path=share_path,
                tag=tag,
                status=initial_status,
                is_backlog=is_first_run,
                file_size_bytes=src_size,
                series_id=series_id,
                series_title=series_title,
                season_number=season_number,
                episode_number=episode_number,
            )
            session.add(item)
            session.commit()
            session.refresh(item)

            if initial_status == "queued":
                logger.info("Queued [%s]: %s", source, title)
                return

    if not copy_enabled:
        return

    # --- Quota gate ---
    # src_size already read above; reuse it to project post-copy usage.
    with get_session() as session:
        db_item = session.get(TrackedItem, item.id)
        if db_item is None:
            return
        pool_is_backlog = db_item.is_backlog
        if not check_quota(session, config, pool_is_backlog, prospective_bytes=src_size):
            pool_label = "backlog" if pool_is_backlog else "new"
            logger.info(
                "Quota full for %s pool — leaving '%s' as pending (will retry).",
                pool_label, title,
            )
            return

    # --- File copy (semaphore limits concurrency) ---
    try:
        async with semaphore:
            # Only mark 'copying' after acquiring the semaphore slot — this
            # prevents items queued behind the semaphore from showing as copying.
            _update_item_status(item.id, "copying", file_size_bytes=src_size)
            await copy_file(
                file_path,
                share_path,
                mode=config.copy_mode,
                item_id=item.id,
                title=title,
            )
        file_size = await asyncio.to_thread(get_file_size, share_path)
        if file_size == 0 or not await asyncio.to_thread(os.path.isfile, share_path):
            raise OSError(f"Post-copy validation failed — file not found at expected share path: {share_path}")
        _update_item_status(item.id, "copied", file_size_bytes=file_size)
        logger.info("Copied [%s]: %s", source, title)
        await notify_copied(config, title, source)
    except Exception as exc:
        _update_item_status(item.id, "error", error_message=str(exc))
        await asyncio.to_thread(cleanup_failed_copy, share_path)
        logger.error("Failed to copy [%s] %s: %s", source, title, exc)
        await notify_error(config, title, source, str(exc))


async def _copy_pending_items(config: AppConfig, semaphore: asyncio.Semaphore) -> None:
    """
    Copy pending items in priority order: non-backlog first, then backlog.

    Pre-selects the items that fit within quota *before* dispatching any copies,
    so concurrent copies cannot collectively exceed the cap.
    """
    with get_session() as session:
        all_pending = session.exec(
            select(TrackedItem)
            .where(TrackedItem.status == "pending")
            .order_by(TrackedItem.is_backlog.asc(), TrackedItem.created_at.asc())
        ).all()

        # ── Quota pre-selection ──────────────────────────────────────────────
        # Walk through candidates in priority order, maintaining a running
        # projection of bytes+files that *will* be added. Items that would
        # exceed the cap are skipped (left as pending for the next poll).
        #
        # We track two budgets independently:
        #   backlog_proj  — bytes/files that will be added to the backlog pool
        #   total_proj    — bytes/files that will be added across both pools

        backlog_usage = get_quota_usage(session, is_backlog=True)
        total_usage   = get_quota_usage(session, is_backlog=None)

        backlog_size_proj  = backlog_usage["size_bytes"]
        backlog_files_proj = backlog_usage["file_count"]
        total_size_proj    = total_usage["size_bytes"]
        total_files_proj   = total_usage["file_count"]

        gb = 1024 ** 3
        backlog_size_limit  = int(config.max_share_size_gb * 0.6 * gb) if config.max_share_size_gb > 0 else 0
        backlog_files_limit = int(config.max_share_files  * 0.6)       if config.max_share_files  > 0 else 0
        total_size_limit    = int(config.max_share_size_gb * gb)        if config.max_share_size_gb > 0 else 0
        total_files_limit   = config.max_share_files

        approved: list[TrackedItem] = []
        for item in all_pending:
            sz = item.file_size_bytes or 0
            # Total cap is a hard limit — check it for every item regardless of pool
            if total_size_limit  and total_size_proj  + sz > total_size_limit:
                logger.info("Total size quota full — skipping '%s' (will retry).", item.title)
                continue
            if total_files_limit and total_files_proj + 1  > total_files_limit:
                logger.info("Total file quota full — skipping '%s' (will retry).", item.title)
                continue
            if item.is_backlog:
                # Backlog also has its own tighter sub-cap (60% of total)
                if backlog_size_limit  and backlog_size_proj  + sz > backlog_size_limit:
                    logger.info("Backlog size quota full — skipping '%s' (will retry).", item.title)
                    continue
                if backlog_files_limit and backlog_files_proj + 1  > backlog_files_limit:
                    logger.info("Backlog file quota full — skipping '%s' (will retry).", item.title)
                    continue
                backlog_size_proj  += sz
                backlog_files_proj += 1

            total_size_proj  += sz
            total_files_proj += 1
            approved.append(item)

    non_backlog = [i for i in approved if not i.is_backlog]
    backlog     = [i for i in approved if i.is_backlog]

    if non_backlog:
        await asyncio.gather(*[_copy_item(config, semaphore, item) for item in non_backlog])
    if backlog:
        await asyncio.gather(*[_copy_item(config, semaphore, item) for item in backlog])


async def _copy_item(config: AppConfig, semaphore: asyncio.Semaphore, item: TrackedItem) -> None:
    """Copy a single pre-approved pending TrackedItem."""
    src_size = item.file_size_bytes or 0

    # Re-check status in case it changed since pre-selection (e.g. manual reset)
    with get_session() as session:
        db_item = session.get(TrackedItem, item.id)
        if db_item is None or db_item.status != "pending":
            return

    try:
        async with semaphore:
            _update_item_status(item.id, "copying", file_size_bytes=src_size)
            await copy_file(
                item.file_path,
                item.share_path,
                mode=config.copy_mode,
                item_id=item.id,
                title=item.title,
            )
        file_size = await asyncio.to_thread(get_file_size, item.share_path)
        if file_size == 0 or not await asyncio.to_thread(os.path.isfile, item.share_path):
            raise OSError(f"Post-copy validation failed — file not found at expected share path: {item.share_path}")
        _update_item_status(item.id, "copied", file_size_bytes=file_size)
        logger.info("Copied [%s]: %s", item.source, item.title)
        await notify_copied(config, item.title, item.source)
    except Exception as exc:
        _update_item_status(item.id, "error", error_message=str(exc))
        await asyncio.to_thread(cleanup_failed_copy, item.share_path)
        logger.error("Failed to copy [%s] %s: %s", item.source, item.title, exc)
        await notify_error(config, item.title, item.source, str(exc))


def _count_new_backlog(source: str) -> int:
    """Return the number of queued backlog items for the given source after a first-run index."""
    with get_session() as session:
        result = session.exec(
            select(func.count()).where(
                TrackedItem.source == source,
                TrackedItem.is_backlog == True,  # noqa: E712
                TrackedItem.status == "queued",
            )
        ).first()
        return result or 0


def _update_item_status(
    item_id: int,
    status: str,
    error_message: Optional[str] = None,
    file_size_bytes: Optional[int] = None,
) -> None:
    """Update the status, optional error message, and optional file size of a TrackedItem."""
    with get_session() as session:
        item = session.get(TrackedItem, item_id)
        if item:
            item.status = status
            item.error_message = error_message
            if file_size_bytes is not None:
                item.file_size_bytes = file_size_bytes
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            session.commit()
