"""Tests for src/models.py — LineData and TradeData dataclasses."""

from datetime import datetime, timezone

from src.domain.models import LineData, TradeData


class TestLineData:

    def test_equality(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        a = LineData(line_id="L1", pair="MNQ", price=100.0, creation_date=dt)
        b = LineData(line_id="L1", pair="MNQ", price=100.0, creation_date=dt)
        assert a == b

    def test_inequality_different_price(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        a = LineData(line_id="L1", pair="MNQ", price=100.0, creation_date=dt)
        b = LineData(line_id="L1", pair="MNQ", price=200.0, creation_date=dt)
        assert a != b

    def test_hash_consistency(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        a = LineData(line_id="L1", pair="MNQ", price=100.0, creation_date=dt)
        b = LineData(line_id="L1", pair="MNQ", price=100.0, creation_date=dt)
        assert hash(a) == hash(b)

    def test_not_equal_to_non_linedata(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        a = LineData(line_id="L1", pair="MNQ", price=100.0, creation_date=dt)
        assert a != "not a LineData"


class TestTradeData:

    def _make(self, **overrides):
        defaults = {
            "trade_id": "T1",
            "pair": "MNQ",
            "trade_type": "long",
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "risk_dollars": None,
            "risk_pct": None,
            "account_balance": None,
            "contracts": None,
            "entry_time": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "exit_price": None,
            "exit_time": None,
            "result": None,
            "result_type": None,
            "fees": None,
            "pnl_usd": None,
            "params": None,
        }
        defaults.update(overrides)
        return TradeData(**defaults)

    def test_equality(self):
        a = self._make()
        b = self._make()
        assert a == b

    def test_inequality(self):
        a = self._make(entry_price=100.0)
        b = self._make(trade_id="T2", entry_price=200.0)
        assert a != b

    def test_hash_usable_in_set(self):
        a = self._make()
        b = self._make()
        assert len({a, b}) == 1

    def test_not_equal_to_non_tradedata(self):
        a = self._make()
        assert a != 42
