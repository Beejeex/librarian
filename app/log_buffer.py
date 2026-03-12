"""
log_buffer.py — In-memory bounded log line store.

Provides a thread-safe deque of recent log lines written during an apply run.
The SSE endpoint reads from this buffer to stream live output to the browser.

A single global instance `log_buffer` is used throughout the application.
"""

import threading
from collections import deque


class LogBuffer:
    """
    Thread-safe bounded buffer of log lines.

    Written to by renamer.py during apply; read by the SSE endpoint.
    Old lines are automatically discarded when maxlen is reached.
    """

    def __init__(self, maxlen: int = 500) -> None:
        self._buffer: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        """Append a log line (thread-safe)."""
        with self._lock:
            self._buffer.append(line)

    def tail(self, n: int = 200) -> list[str]:
        """Return the last n lines as a list (thread-safe)."""
        with self._lock:
            items = list(self._buffer)
        return items[-n:] if len(items) > n else items

    def clear(self) -> None:
        """Clear all buffered lines (thread-safe)."""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


# Global singleton used by renamer.py and the SSE endpoint
log_buffer = LogBuffer()
