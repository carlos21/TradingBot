"""Tests for src/services/trade_executor.py."""

from src.services.trade_executor import NoOpExecutor


class TestNoOpExecutor:

    def test_on_trade_open_no_error(self):
        ex = NoOpExecutor()
        ex.on_trade_open({"trade_id": "T1"})

    def test_on_trade_close_no_error(self):
        ex = NoOpExecutor()
        ex.on_trade_close("T1", 100.0)

    def test_on_sl_update_no_error(self):
        ex = NoOpExecutor()
        ex.on_sl_update("T1", 95.0)


