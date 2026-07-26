"""Tests for HistoryLoadedDeduper."""

from src.utils.history_loaded_deduper import HistoryLoadedDeduper
from tests.fakes import DummySocketIO


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

    def test_identical_payloads_for_different_pairs_both_emit(self):
        """Same-session instruments can produce identical signatures (same bar
        count, same last bar time); one must never swallow the other's event."""
        sio = DummySocketIO()
        d = HistoryLoadedDeduper()
        base = {"readiness_state": "WARMING_UP", "readiness_reason": "r1",
                "bar_count": 28740, "last_bar_time": 1234567890}
        d.emit(sio, {**base, "pair": "MES"})
        d.emit(sio, {**base, "pair": "MNQ"})
        assert len(sio.events) == 2
        assert sio.events[0][1]["pair"] == "MES"
        assert sio.events[1][1]["pair"] == "MNQ"

    def test_same_pair_identical_payload_still_dedupes(self):
        sio = DummySocketIO()
        d = HistoryLoadedDeduper()
        payload = {"readiness_state": "WARMING_UP", "readiness_reason": "r1",
                   "bar_count": 10, "pair": "MNQ"}
        d.emit(sio, payload)
        d.emit(sio, payload)
        assert len(sio.events) == 1

    def test_same_pair_dedupes_independently(self):
        """A changed payload for one pair does not unblock a duplicate of another."""
        sio = DummySocketIO()
        d = HistoryLoadedDeduper()
        mes = {"readiness_state": "WARMING_UP", "readiness_reason": "r1",
               "bar_count": 10, "pair": "MES"}
        mnq = {"readiness_state": "WARMING_UP", "readiness_reason": "r1",
               "bar_count": 20, "pair": "MNQ"}
        d.emit(sio, mes)
        d.emit(sio, mnq)
        d.emit(sio, {"readiness_state": "READY", "readiness_reason": "r2",
                     "bar_count": 10, "pair": "MES"})
        d.emit(sio, mnq)  # duplicate of MNQ's last signature
        assert [e[1]["pair"] for e in sio.events] == ["MES", "MNQ", "MES"]
