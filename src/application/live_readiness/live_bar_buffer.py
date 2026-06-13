"""Buffer live bars that arrive while the system is warming up."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class LiveBarBuffer:
    """
    Holds live bars received while the readiness state machine is not yet
    READY/LIVE and flushes them in chronological order once it is.

    In normal operation warm-up lasts well under a second, so the buffer
    only contains a handful of bars.
    """

    def __init__(self, processor: Callable[[dict[str, Any]], None]) -> None:
        self._processor = processor
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, bar: dict[str, Any]) -> None:
        """Store a bar for later processing."""
        with self._lock:
            self._buffer.append(bar)

    def clear(self) -> None:
        """Drop all buffered bars without processing them."""
        with self._lock:
            self._buffer.clear()

    def flush(self) -> None:
        """Process all buffered bars in order and clear the buffer."""
        with self._lock:
            bars = list(self._buffer)
            self._buffer.clear()

        for bar in bars:
            self._processor(bar)

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)
