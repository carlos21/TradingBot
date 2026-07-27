"""Tests for SocketIOReadinessProgressAdapter."""

from src.infrastructure.readiness_progress_adapter import (
    SocketIOReadinessProgressAdapter,
)


class _RecordingSocketIO:
    def __init__(self):
        self.events = []

    def emit(self, event, payload, **kwargs):
        self.events.append((event, payload, kwargs))


def test_adapter_emits_warmup_progress() -> None:
    socketio = _RecordingSocketIO()
    adapter = SocketIOReadinessProgressAdapter(socketio)

    adapter.emit_warmup_progress(25, 100)

    assert len(socketio.events) == 1
    event, payload, kwargs = socketio.events[0]
    assert event == "warmup_progress"
    assert payload["phase"] == "warmup"
    assert payload["current"] == 25
    assert payload["total"] == 100
    assert payload["percent"] == 25
    assert kwargs == {}


def test_adapter_emits_phase_started() -> None:
    socketio = _RecordingSocketIO()
    adapter = SocketIOReadinessProgressAdapter(socketio)

    adapter.emit_phase_started("refreshing", "Historical data refresh started")

    assert len(socketio.events) == 1
    event, payload, kwargs = socketio.events[0]
    assert event == "phase_started"
    assert payload["phase"] == "refreshing"
    assert payload["reason"] == "Historical data refresh started"
    assert kwargs == {}


def test_adapter_emits_to_room_when_configured() -> None:
    socketio = _RecordingSocketIO()
    adapter = SocketIOReadinessProgressAdapter(socketio, room="MNQ")

    adapter.emit_warmup_progress(25, 100)

    assert len(socketio.events) == 1
    event, payload, kwargs = socketio.events[0]
    assert event == "warmup_progress"
    assert kwargs == {"room": "MNQ"}
