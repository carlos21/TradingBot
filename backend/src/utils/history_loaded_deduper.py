"""Deduplicate history_loaded emissions across reconnects."""

import threading
from typing import Any


class HistoryLoadedDeduper:
    """
    Server-side dedupe for ``history_loaded`` Socket.IO events.

    Both the readiness monitor and the connect handler emit ``history_loaded``.
    Without deduplication, a reconnecting browser receives the same payload
    twice (once from the monitor's last completion and once from connect).

    Deduping is per instrument (keyed by the payload's ``pair``): different
    instruments may legitimately produce identical readiness state, reason,
    bar count and last-bar time (e.g. same-session futures refreshed over a
    weekend), and one instrument's event must never swallow another's.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_signatures: dict[Any, tuple[Any, ...]] = {}

    def _signature(self, payload: dict[str, Any]) -> tuple[Any, ...]:
        return (
            payload.get("readiness_state"),
            payload.get("readiness_reason"),
            payload.get("bar_count"),
            payload.get("last_bar_time"),
        )

    def emit(self, socketio, payload: dict[str, Any]) -> None:
        """Emit ``history_loaded`` only if the payload changed for its pair."""
        pair = payload.get("pair")
        signature = self._signature(payload)
        with self._lock:
            if signature == self._last_signatures.get(pair):
                return
            self._last_signatures[pair] = signature
        socketio.emit("history_loaded", payload)
