"""Track readiness state progression and compute overall completion percentage."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from src.domain.readiness import ReadinessState


@dataclass(frozen=True)
class PhaseProgress:
    """Value object for the progress of a single readiness phase."""

    phase: str
    current: int
    total: int

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return int((self.current / self.total) * 100)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "current": self.current,
            "total": self.total,
            "percent": self.percent,
        }


class ReadinessProgressTracker:
    """Maps readiness state + optional phase progress to an overall 0-100 score.

    This class is the single source of truth for "how close is the system to
    being ready to trade". It is intentionally decoupled from SocketIO and
    from the state machine itself; callers feed it state changes and phase
    progress, and it exposes a snapshot for health payloads.
    """

    _STATE_BASE_PERCENT: dict[ReadinessState, int] = {
        ReadinessState.DISCONNECTED: 0,
        ReadinessState.CONNECTED: 15,
        ReadinessState.WAITING_FOR_HISTORY: 25,
        ReadinessState.REFRESHING: 40,
        ReadinessState.WARMING_UP: 60,
        ReadinessState.READY: 95,
        ReadinessState.LIVE: 100,
        ReadinessState.DEGRADED: 95,
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = ReadinessState.DISCONNECTED
        self._reason = "Initializing"
        self._phase_progress: PhaseProgress | None = None

    def update_state(self, state: ReadinessState, reason: str) -> None:
        """Record a readiness state change."""
        with self._lock:
            self._state = state
            self._reason = reason
            # Phases that are not themselves progress-bar friendly clear any
            # previous phase progress so the UI does not show stale warmup bars.
            if state in (
                ReadinessState.CONNECTED,
                ReadinessState.WAITING_FOR_HISTORY,
                ReadinessState.READY,
                ReadinessState.LIVE,
            ):
                self._phase_progress = None

    def update_warmup_progress(self, current: int, total: int) -> None:
        """Record the latest warmup replay progress."""
        with self._lock:
            self._phase_progress = PhaseProgress("warmup", current, total)

    def on_warmup_progress(self, current: int, total: int) -> None:
        """Convenience alias so the tracker can act as an IWarmupProgressListener."""
        self.update_warmup_progress(current, total)

    def update_phase_started(self, phase: str, reason: str) -> None:
        """Record that a new long-running phase has started."""
        with self._lock:
            self._phase_progress = PhaseProgress(phase, 0, 0)
            self._reason = reason

    def get_snapshot(self) -> dict[str, Any]:
        """Return a serializable snapshot of current readiness progress."""
        with self._lock:
            base = self._STATE_BASE_PERCENT.get(self._state, 0)
            phase = self._phase_progress
            phase_percent = 0
            if phase is not None:
                phase_percent = phase.percent

            # WARMING_UP gets an additional 0-30% boost from replay progress.
            if self._state == ReadinessState.WARMING_UP and phase is not None:
                overall = base + int(phase_percent * 0.30)
            else:
                overall = base

            return {
                "readiness_state": self._state.name,
                "readiness_reason": self._reason,
                "readiness_percent": min(overall, 100),
                "phase": phase.phase if phase else None,
                "phase_progress": phase.to_dict() if phase else None,
            }
