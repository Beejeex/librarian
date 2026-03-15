"""
log_buffer.py — In-memory bounded log line stores.

Provides thread-safe deques of recent log lines.  The SSE endpoints read from
these buffers to stream live output to the browser.

Two separate global instances are maintained so Renamer and Tracker output
never cross-contaminate each other's UI streams:

  log_buffer         — Renamer apply output (written by renamer.py directly
                        and by the LogHandler on app.renamer / app.scanner /
                        app.radarr / app.sonarr / app.arr_client loggers).

  tracker_log_buffer — Tracker poll/copy/watcher output (written by the
                        LogHandler on app.scheduler / app.watcher /
                        app.copier / app.notifier loggers).

A Python logging.Handler subclass (LogHandler) pushes log records from the
standard library logging system into the appropriate buffer; see main.py.
"""

import asyncio
import logging
import threading
from collections import deque


class LogBuffer:
    """
    Thread-safe bounded buffer of log lines.

    Written to by renamer.py during apply and by LogHandler for all logger
    output; read by the SSE endpoints.
    Old lines are automatically discarded when maxlen is reached.
    """

    def __init__(self, maxlen: int = 500) -> None:
        self._buffer: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        # Async listeners: a list of asyncio.Queue objects — one per SSE client.
        # Each write fans out to all active queues.
        self._queues: list[asyncio.Queue] = []
        self._queues_lock = threading.Lock()

    def append(self, line: str) -> None:
        """Append a log line (thread-safe). Fans out to any SSE subscriber queues."""
        with self._lock:
            self._buffer.append(line)
        # Fan-out to all asyncio Queues (SSE clients).
        # put_nowait is used so the calling thread is never blocked.
        with self._queues_lock:
            live = []
            for q in self._queues:
                try:
                    q.put_nowait(line)
                    live.append(q)
                except asyncio.QueueFull:
                    live.append(q)
                except Exception:
                    pass  # Dead queue — do not keep it
            self._queues = live

    def tail(self, n: int = 200) -> list[str]:
        """Return the last n lines as a list (thread-safe)."""
        with self._lock:
            items = list(self._buffer)
        return items[-n:] if len(items) > n else items

    def clear(self) -> None:
        """Clear all buffered lines (thread-safe)."""
        with self._lock:
            self._buffer.clear()

    def subscribe(self) -> asyncio.Queue:
        """
        Return a new asyncio.Queue that will receive all future appended lines.
        Call unsubscribe() when the SSE client disconnects to avoid leaking queues.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._queues_lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        with self._queues_lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


class LogHandler(logging.Handler):
    """
    Python logging.Handler that writes records into a LogBuffer.

    Install with:
        logging.getLogger().addHandler(LogHandler(log_buffer))
    """

    def __init__(self, buffer: LogBuffer, level: int = logging.DEBUG) -> None:
        super().__init__(level)
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            self._buffer.append(line)
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
# Global singletons — one per subsystem so Renamer and Tracker streams
# are kept completely separate.
# ---------------------------------------------------------------------------

# Renamer apply output — written by renamer.py directly and by the
# LogHandler installed on renamer-specific loggers in main.py.
log_buffer = LogBuffer()

# Tracker poll/copy/watcher output — written by the LogHandler installed
# on tracker-specific loggers in main.py.
tracker_log_buffer = LogBuffer()


# ---------------------------------------------------------------------------
# Renamer convenience wrappers (used by api.py)
# ---------------------------------------------------------------------------

def get_recent_logs(n: int = 100) -> list[str]:
    """Return the last n lines from the renamer log buffer."""
    return log_buffer.tail(n)


def clear_logs() -> None:
    """Clear the renamer log buffer."""
    log_buffer.clear()


def get_log_queue() -> asyncio.Queue:
    """
    Return a new async queue subscribed to all future renamer log lines.

    The caller MUST call unsubscribe_log_queue(q) when done to avoid leaks.
    """
    return log_buffer.subscribe()


def unsubscribe_log_queue(q: asyncio.Queue) -> None:
    """Unsubscribe a previously subscribed renamer queue."""
    log_buffer.unsubscribe(q)


# ---------------------------------------------------------------------------
# Tracker convenience wrappers (used by tracker_api.py)
# ---------------------------------------------------------------------------

def get_recent_tracker_logs(n: int = 100) -> list[str]:
    """Return the last n lines from the tracker log buffer."""
    return tracker_log_buffer.tail(n)


def clear_tracker_logs() -> None:
    """Clear the tracker log buffer."""
    tracker_log_buffer.clear()


def get_tracker_log_queue() -> asyncio.Queue:
    """
    Return a new async queue subscribed to all future tracker log lines.

    The caller MUST call unsubscribe_tracker_log_queue(q) when done.
    """
    return tracker_log_buffer.subscribe()


def unsubscribe_tracker_log_queue(q: asyncio.Queue) -> None:
    """Unsubscribe a previously subscribed tracker queue."""
    tracker_log_buffer.unsubscribe(q)

