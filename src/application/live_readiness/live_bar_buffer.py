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

    def __init__(
        self,
        processor: Callable[[dict[str, Any]], None],
        logger: Any | None = None,
    ) -> None:
        self._processor = processor
        self._logger = logger
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_historical_time: float | int | None = None

    def set_last_historical_time(self, t: float | None) -> None:
        """Drop buffered bars at or before this time on flush (replay→live guard)."""
        with self._lock:
            self._last_historical_time = t

    def append(self, bar: dict[str, Any]) -> None:
        """Store a bar for later processing."""
        with self._lock:
            self._buffer.append(bar)

    def clear(self) -> None:
        """Drop all buffered bars without processing them."""
        with self._lock:
            self._buffer.clear()

    def flush(self) -> None:
        """Process all buffered bars in chronological order and clear the buffer.

        Bars are sorted by time, deduplicated, and filtered against the
        last-historical-time watermark. Processor exceptions are logged and
        processing continues with the remaining bars.
        """
        with self._lock:
            bars = list(self._buffer)
            self._buffer.clear()
            watermark = self._last_historical_time

        bars.sort(key=lambda b: b["time"])
        seen: set[float | int] = set()
        for bar in bars:
            t = bar["time"]
            if t in seen:
                continue
            seen.add(t)
            if watermark is not None and t <= watermark:
                continue
            try:
                self._processor(bar)
            except Exception as e:
                if self._logger:
                    self._logger.error(f"[LiveBarBuffer] Failed to process bar {t}: {e}")

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)
