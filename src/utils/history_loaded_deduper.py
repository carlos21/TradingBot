"""Deduplicate history_loaded emissions across reconnects."""

import threading
from typing import Any


class HistoryLoadedDeduper:
    """
    Server-side dedupe for ``history_loaded`` Socket.IO events.

    Both the readiness monitor and the connect handler emit ``history_loaded``.
    Without deduplication, a reconnecting browser receives the same payload
    twice (once from the monitor's last completion and once from connect).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_signature: tuple[Any, ...] | None = None

    def _signature(self, payload: dict[str, Any]) -> tuple[Any, ...]:
        return (
            payload.get("readiness_state"),
            payload.get("readiness_reason"),
            payload.get("bar_count"),
            payload.get("last_bar_time"),
        )

    def emit(self, socketio, payload: dict[str, Any]) -> None:
        """Emit ``history_loaded`` only if the payload has changed."""
        signature = self._signature(payload)
        with self._lock:
            if signature == self._last_signature:
                return
            self._last_signature = signature
        socketio.emit("history_loaded", payload)
