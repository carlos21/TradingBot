"""Tests for HistoryLoadedDeduper."""

from tests.fakes import DummySocketIO
from src.utils.history_loaded_deduper import HistoryLoadedDeduper


class TestHistoryLoadedDeduper:
    def test_emits_first_payload(self):
        sio = DummySocketIO()
        d = HistoryLoadedDeduper()
        d.emit(sio, {"readiness_state": "WARMING_UP", "readiness_reason": "r1", "bar_count": 10})
        assert len(sio.events) == 1
        assert sio.events[0][0] == "history_loaded"

    def test_skips_duplicate_payload(self):
        sio = DummySocketIO()
        d = HistoryLoadedDeduper()
        payload = {"readiness_state": "WARMING_UP", "readiness_reason": "r1", "bar_count": 10}
        d.emit(sio, payload)
        d.emit(sio, payload)
        assert len(sio.events) == 1

    def test_emits_when_payload_changes(self):
        sio = DummySocketIO()
        d = HistoryLoadedDeduper()
        d.emit(sio, {"readiness_state": "WARMING_UP", "readiness_reason": "r1", "bar_count": 10})
        d.emit(sio, {"readiness_state": "READY", "readiness_reason": "r1", "bar_count": 10})
        assert len(sio.events) == 2
