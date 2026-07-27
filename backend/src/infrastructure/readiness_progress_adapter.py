"""Infrastructure adapter that forwards readiness progress to SocketIO."""

from __future__ import annotations

from typing import Any

from src.application.ports import IReadinessProgressEmitter


class SocketIOReadinessProgressAdapter(IReadinessProgressEmitter):
    """Emits readiness progress events through a SocketIO-compatible object.

    When a ``room`` is supplied, events are scoped to that instrument room so
    multi-instrument tabs only see the readiness progress for their own symbol.
    """

    def __init__(self, socketio: Any, room: str | None = None) -> None:
        self._socketio = socketio
        self._room = room

    def emit_warmup_progress(self, current: int, total: int) -> None:
        payload = {
            "phase": "warmup",
            "current": current,
            "total": total,
            "percent": int((current / total) * 100) if total > 0 else 0,
        }
        if self._room:
            self._socketio.emit("warmup_progress", payload, room=self._room)
        else:
            self._socketio.emit("warmup_progress", payload)

    def emit_phase_started(self, phase: str, reason: str) -> None:
        payload = {
            "phase": phase,
            "reason": reason,
        }
        if self._room:
            self._socketio.emit("phase_started", payload, room=self._room)
        else:
            self._socketio.emit("phase_started", payload)
