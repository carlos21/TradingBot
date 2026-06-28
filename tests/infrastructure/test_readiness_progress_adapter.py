"""Tests for SocketIOReadinessProgressAdapter."""

from src.infrastructure.readiness_progress_adapter import (
    SocketIOReadinessProgressAdapter,
)
from tests.fakes import DummySocketIO


def test_adapter_emits_warmup_progress() -> None:
    socketio = DummySocketIO()
    adapter = SocketIOReadinessProgressAdapter(socketio)

    adapter.emit_warmup_progress(25, 100)

    assert len(socketio.events) == 1
    event, payload = socketio.events[0]
    assert event == "warmup_progress"
    assert payload["phase"] == "warmup"
    assert payload["current"] == 25
    assert payload["total"] == 100
    assert payload["percent"] == 25


def test_adapter_emits_phase_started() -> None:
    socketio = DummySocketIO()
    adapter = SocketIOReadinessProgressAdapter(socketio)

    adapter.emit_phase_started("refreshing", "Historical data refresh started")

    assert len(socketio.events) == 1
    event, payload = socketio.events[0]
    assert event == "phase_started"
    assert payload["phase"] == "refreshing"
    assert payload["reason"] == "Historical data refresh started"
