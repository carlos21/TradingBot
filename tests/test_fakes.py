"""Tests for tests/fakes.py — ensure fake repositories behave correctly for test use."""

from datetime import datetime, timezone
from tests.fakes import FakeLineRepository, FakeTradeRepository, FakeTradeExecutor, FakeAnalyticsReporter, DummySocketIO
from src.models import TradeData, LineData


class TestFakeLineRepository:

    def test_insert_and_list(self):
        repo = FakeLineRepository()
        line = repo.insert_line("NQ", 100.0)
        assert isinstance(line, LineData)
        assert line.price == 100.0
        lines = repo.list_lines("NQ")
        assert len(lines) == 1

    def test_list_filters_by_pair(self):
        repo = FakeLineRepository()
        repo.insert_line("NQ", 100.0)
        repo.insert_line("ES", 200.0)
        assert len(repo.list_lines("NQ")) == 1
        assert len(repo.list_lines("ES")) == 1

    def test_delete(self):
        repo = FakeLineRepository()
        line = repo.insert_line("NQ", 100.0)
        repo.delete_line(line.line_id)
        assert len(repo.list_lines("NQ")) == 0

    def test_delete_nonexistent_no_error(self):
        repo = FakeLineRepository()
        repo.delete_line("DOES_NOT_EXIST")


class TestFakeTradeRepository:

    def test_insert_returns_trade_data(self):
        repo = FakeTradeRepository()
        td = repo.insert_trade("NQ", "long", 100, 90, 130, 10,
                               datetime(2025, 1, 1, tzinfo=timezone.utc))
        assert isinstance(td, TradeData)
        assert td.trade_id == "T1"

    def test_list_trades(self):
        repo = FakeTradeRepository()
        repo.insert_trade("NQ", "long", 100, 90, 130, 10,
                          datetime(2025, 1, 1, tzinfo=timezone.utc))
        trades = repo.list_trades("NQ")
        assert len(trades) == 1
        assert isinstance(trades[0], TradeData)

    def test_close_trade(self):
        repo = FakeTradeRepository()
        repo.insert_trade("NQ", "long", 100, 90, 130, 10,
                          datetime(2025, 1, 1, tzinfo=timezone.utc))
        repo.close_trade("T1", 120, datetime(2025, 1, 2, tzinfo=timezone.utc), 2.0, "TP")
        trades = repo.list_trades("NQ")
        assert trades[0].exit_price == 120
        assert trades[0].result == 2.0

    def test_update_stop_loss(self):
        repo = FakeTradeRepository()
        repo.insert_trade("NQ", "long", 100, 90, 130, 10,
                          datetime(2025, 1, 1, tzinfo=timezone.utc))
        repo.update_stop_loss("T1", 95)
        assert repo.inserted[0]["stop_loss"] == 95

    def test_update_entry_price(self):
        repo = FakeTradeRepository()
        repo.insert_trade("NQ", "long", 100, 90, 130, 10,
                          datetime(2025, 1, 1, tzinfo=timezone.utc))
        repo.update_entry_price("T1", 101)
        assert repo.inserted[0]["entry"] == 101

    def test_append_and_get_trade_logs(self):
        repo = FakeTradeRepository()
        repo.insert_trade("NQ", "long", 100, 90, 130, 10,
                          datetime(2025, 1, 1, tzinfo=timezone.utc))
        repo.append_trade_log("T1", "OPEN", "opened")
        logs = repo.get_trade_logs("T1")
        assert len(logs) == 1

    def test_clear(self):
        repo = FakeTradeRepository()
        repo.insert_trade("NQ", "long", 100, 90, 130, 10,
                          datetime(2025, 1, 1, tzinfo=timezone.utc))
        repo.clear()
        assert len(repo.inserted) == 0
        assert repo._seq == 0


class TestDummySocketIO:

    def test_emit_records_events(self):
        sio = DummySocketIO()
        sio.emit("test_event", {"data": 1})
        assert len(sio.events) == 1
        assert sio.events[0] == ("test_event", {"data": 1})

    def test_start_background_task_runs_sync(self):
        sio = DummySocketIO()
        results = []
        sio.start_background_task(lambda: results.append(1))
        assert results == [1]


class TestFakeAnalyticsReporter:

    def test_records_exceptions(self):
        a = FakeAnalyticsReporter()
        a.capture_exception(ValueError("test"), {"op": "test"})
        assert len(a.exceptions) == 1
        assert isinstance(a.exceptions[0][0], ValueError)

    def test_records_trade_events(self):
        a = FakeAnalyticsReporter()
        a.capture_trade_event("TRADE_OPEN", {"trade_id": "T1"})
        assert len(a.trade_events) == 1
        assert a.trade_events[0] == ("TRADE_OPEN", {"trade_id": "T1"})

    def test_records_signal_events(self):
        a = FakeAnalyticsReporter()
        a.capture_signal_event("LATCH", {"line_id": "L1"})
        assert len(a.signal_events) == 1

    def test_records_context(self):
        a = FakeAnalyticsReporter()
        a.set_context("app", {"pair": "NQ"})
        assert a.contexts["app"]["pair"] == "NQ"


class TestFakeTradeExecutor:

    def test_records_opens(self):
        ex = FakeTradeExecutor()
        ex.on_trade_open({"trade_id": "T1"})
        assert len(ex.opens) == 1

    def test_records_closes(self):
        ex = FakeTradeExecutor()
        ex.on_trade_close("T1", 100.0)
        assert len(ex.closes) == 1

    def test_records_sl_updates(self):
        ex = FakeTradeExecutor()
        ex.on_sl_update("T1", 95.0)
        assert len(ex.sl_updates) == 1
