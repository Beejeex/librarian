"""
In-memory log buffer for MadTracked.

Captures the last N log lines so the UI /logs page can display recent activity
without needing to read from disk or stream a file.

Also exposes an asyncio.Queue that receives every new log line, used by the
SSE endpoint to push live log output to connected browser clients.
"""

import asyncio
import logging
from collections import deque

# Keep the last 500 log lines in memory
_log_buffer: deque[str] = deque(maxlen=500)

# Unbounded queue: SSE consumers drain it; lines are never dropped here
_log_queue: asyncio.Queue = asyncio.Queue(maxsize=0)


class MemoryLogHandler(logging.Handler):
    """Logging handler that appends formatted records to the buffer and SSE queue."""

    def emit(self, record: logging.LogRecord) -> None:
        """Format the record, push to the shared deque, and enqueue for SSE consumers."""
        line = self.format(record)
        _log_buffer.append(line)
        # put_nowait is safe here — queue is unbounded so it never raises QueueFull
        try:
            _log_queue.put_nowait(line)
        except asyncio.QueueFull:
            pass  # Should never happen with maxsize=0, but be defensive


def get_recent_logs(n: int = 100) -> list[str]:
    """
    Return the last n log lines from the in-memory buffer.

    Args:
        n: Maximum number of lines to return (newest last).
    """
    lines = list(_log_buffer)
    return lines[-n:]


def clear_logs() -> None:
    """Wipe all entries from the in-memory log buffer."""
    _log_buffer.clear()


def get_log_queue() -> asyncio.Queue:
    """
    Return the shared asyncio.Queue that receives every new log line.

    Used exclusively by the SSE log-stream endpoint to push live output
    to connected browser clients.
    """
    return _log_queue


def setup_memory_handler() -> None:
    """Attach the MemoryLogHandler to the root logger so all log output is captured."""
    handler = MemoryLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
