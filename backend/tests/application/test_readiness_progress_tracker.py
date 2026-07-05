"""Tests for ReadinessProgressTracker and WarmupProgressReporter."""

from src.application.live_readiness.readiness_progress_tracker import (
    PhaseProgress,
    ReadinessProgressTracker,
)
from src.application.live_readiness.warmup_progress_reporter import (
    WarmupProgressMulticaster,
    WarmupProgressReporter,
)
from src.domain.readiness import ReadinessState


class _FakeEmitter:
    """Test double for IReadinessProgressEmitter."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit_warmup_progress(self, current: int, total: int) -> None:
        self.events.append(("warmup_progress", {"current": current, "total": total}))

    def emit_phase_started(self, phase: str, reason: str) -> None:
        self.events.append(("phase_started", {"phase": phase, "reason": reason}))


def test_phase_progress_percent() -> None:
    p = PhaseProgress("warmup", 25, 100)
    assert p.percent == 25


def test_phase_progress_percent_zero_total() -> None:
    p = PhaseProgress("warmup", 0, 0)
    assert p.percent == 0


def test_tracker_initial_snapshot() -> None:
    tracker = ReadinessProgressTracker()
    snapshot = tracker.get_snapshot()

    assert snapshot["readiness_state"] == "DISCONNECTED"
    assert snapshot["readiness_percent"] == 0
    assert snapshot["phase"] is None
    assert snapshot["phase_progress"] is None


def test_tracker_state_progression() -> None:
    tracker = ReadinessProgressTracker()

    tracker.update_state(ReadinessState.CONNECTED, "Platform connected")
    assert tracker.get_snapshot()["readiness_percent"] == 15

    tracker.update_state(ReadinessState.REFRESHING, "Refresh started")
    assert tracker.get_snapshot()["readiness_percent"] == 40

    tracker.update_state(ReadinessState.WARMING_UP, "Warming up")
    assert tracker.get_snapshot()["readiness_percent"] == 60


def test_tracker_warmup_progress_boosts_overall_percent() -> None:
    tracker = ReadinessProgressTracker()
    tracker.update_state(ReadinessState.WARMING_UP, "Warming up")
    tracker.update_warmup_progress(50, 100)

    snapshot = tracker.get_snapshot()
    assert snapshot["readiness_percent"] == 75  # 60 + 50 * 0.30
    assert snapshot["phase"] == "warmup"
    assert snapshot["phase_progress"]["percent"] == 50


def test_tracker_phase_started() -> None:
    tracker = ReadinessProgressTracker()
    tracker.update_phase_started("refreshing", "Refresh started")

    snapshot = tracker.get_snapshot()
    assert snapshot["phase"] == "refreshing"
    assert snapshot["phase_progress"]["current"] == 0


def test_tracker_capped_at_100() -> None:
    tracker = ReadinessProgressTracker()
    tracker.update_state(ReadinessState.WARMING_UP, "Warming up")
    tracker.update_warmup_progress(200, 100)

    assert tracker.get_snapshot()["readiness_percent"] == 100


def test_tracker_state_change_clears_phase_progress() -> None:
    tracker = ReadinessProgressTracker()
    tracker.update_state(ReadinessState.WARMING_UP, "Warming up")
    tracker.update_warmup_progress(50, 100)
    tracker.update_state(ReadinessState.READY, "Ready")

    snapshot = tracker.get_snapshot()
    assert snapshot["phase"] is None
    assert snapshot["phase_progress"] is None


def test_warmup_progress_reporter_forwards_to_emitter() -> None:
    emitter = _FakeEmitter()
    reporter = WarmupProgressReporter(emitter)

    reporter.on_warmup_progress(10, 100)

    assert len(emitter.events) == 1
    event, payload = emitter.events[0]
    assert event == "warmup_progress"
    assert payload["current"] == 10
    assert payload["total"] == 100


def test_tracker_acts_as_warmup_progress_listener() -> None:
    tracker = ReadinessProgressTracker()
    tracker.update_state(ReadinessState.WARMING_UP, "Warming up")

    tracker.on_warmup_progress(25, 100)

    snapshot = tracker.get_snapshot()
    assert snapshot["phase_progress"]["percent"] == 25


def test_warmup_progress_multicaster_updates_multiple_listeners() -> None:
    tracker = ReadinessProgressTracker()
    tracker.update_state(ReadinessState.WARMING_UP, "Warming up")
    emitter = _FakeEmitter()
    reporter = WarmupProgressReporter(emitter)
    multicaster = WarmupProgressMulticaster([tracker, reporter])

    multicaster.on_warmup_progress(50, 100)

    assert tracker.get_snapshot()["phase_progress"]["percent"] == 50
    assert len(emitter.events) == 1
    assert emitter.events[0][0] == "warmup_progress"
