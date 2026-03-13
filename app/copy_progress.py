"""
In-memory registry tracking active file copies for the UI progress indicator.

Updated by copier.py (on the thread-pool copy thread) and read by the
tracker logs SSE route. Thread-safe via a Lock.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class _CopyJob:
    """Holds the live state of a single in-progress file copy."""
    item_id: int
    title: str
    filename: str
    total_bytes: int
    bytes_done: int = 0
    speed_bps: float = 0.0
    started_at: float = field(default_factory=time.monotonic)


_registry: Dict[int, _CopyJob] = {}
_lock = threading.Lock()


def start(item_id: int, title: str, filename: str, total_bytes: int) -> None:
    """Register a new copy job. Call once before the copy loop begins."""
    with _lock:
        _registry[item_id] = _CopyJob(
            item_id=item_id,
            title=title,
            filename=filename,
            total_bytes=total_bytes,
        )


def update(item_id: int, bytes_done: int, speed_bps: float) -> None:
    """Update progress for an active job. Called after each chunk is written."""
    with _lock:
        job = _registry.get(item_id)
        if job:
            job.bytes_done = bytes_done
            job.speed_bps = speed_bps


def finish(item_id: int) -> None:
    """Remove a job from the registry once its copy completes or fails."""
    with _lock:
        _registry.pop(item_id, None)


def get_all() -> list:
    """
    Return a snapshot of all active copy jobs as a list of dicts for the template.

    Keys per dict: item_id, title, filename, pct (0-100), speed_str,
    bytes_done, total_bytes.
    """
    with _lock:
        result = []
        for job in list(_registry.values()):
            pct = int(job.bytes_done * 100 / job.total_bytes) if job.total_bytes > 0 else 0
            speed_mbps = job.speed_bps / (1024 * 1024)
            if speed_mbps >= 1.0:
                speed_str = f"{speed_mbps:.1f} MB/s"
            else:
                speed_str = f"{job.speed_bps / 1024:.0f} KB/s"
            result.append({
                "item_id": job.item_id,
                "title": job.title,
                "filename": job.filename,
                "pct": pct,
                "speed_str": speed_str,
                "bytes_done": job.bytes_done,
                "total_bytes": job.total_bytes,
            })
        return result


def clear() -> None:
    """Remove all entries — called on startup to clear stale state from a prior crash."""
    with _lock:
        _registry.clear()
