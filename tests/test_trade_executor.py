"""Tests for src/services/trade_executor.py."""

from src.services.trade_executor import NoOpExecutor, NinjaTraderExecutor


class FakeDataSource:
    def __init__(self):
        self.commands = []

    def enqueue_command(self, cmd):
        self.commands.append(cmd)


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


class TestNinjaTraderExecutor:

    def test_on_trade_open_sends_sl_points_and_rr_ratio(self):
        ds = FakeDataSource()
        ex = NinjaTraderExecutor(ds)
        trade = {
            "trade_id": "T1", "pair": "NQ", "type": "long",
            "entry": 21000.0, "risk": 80.0, "rr_ratio": 3.3,
            "stop_loss": 20920.0, "take_profit": 21264.0,
        }
        ex.on_trade_open(trade)
        cmd = ds.commands[0]
        assert cmd["command"] == "place_order"
        assert cmd["sl_points"] == 80.0
        assert cmd["rr_ratio"] == 3.3
        assert cmd["entry_price"] == 21000.0
        assert cmd["direction"] == "long"
        assert "stop_loss" not in cmd
        assert "take_profit" not in cmd

    def test_on_trade_open_defaults_rr_ratio(self):
        ds = FakeDataSource()
        ex = NinjaTraderExecutor(ds)
        trade = {
            "trade_id": "T1", "pair": "NQ", "type": "short",
            "entry": 21000.0, "risk": 80.0,
            "stop_loss": 21080.0, "take_profit": 20680.0,
        }
        ex.on_trade_open(trade)
        assert ds.commands[0]["rr_ratio"] == 5.0
