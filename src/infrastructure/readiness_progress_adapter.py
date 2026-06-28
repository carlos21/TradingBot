"""Infrastructure adapter that forwards readiness progress to SocketIO."""

from __future__ import annotations

from typing import Any

from src.application.ports import IReadinessProgressEmitter


class SocketIOReadinessProgressAdapter(IReadinessProgressEmitter):
    """Emits readiness progress events through a SocketIO-compatible object."""

    def __init__(self, socketio: Any) -> None:
        self._socketio = socketio

    def emit_warmup_progress(self, current: int, total: int) -> None:
        self._socketio.emit(
            "warmup_progress",
            {
                "phase": "warmup",
                "current": current,
                "total": total,
                "percent": int((current / total) * 100) if total > 0 else 0,
            },
        )

    def emit_phase_started(self, phase: str, reason: str) -> None:
        self._socketio.emit(
            "phase_started",
            {
                "phase": phase,
                "reason": reason,
            },
        )
