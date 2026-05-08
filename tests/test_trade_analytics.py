"""Tests for src/trade_analytics.py."""

import pytest

from src.trade_analytics import (
    DistributionData,
    TimeSeriesData,
    TradeDetail,
    TradeStatistics,
)


class TestTradeStatistics:

    def test_to_dict(self):
        stats = TradeStatistics(
            total_trades=10,
            open_trades=2,
            winning_trades=6,
            losing_trades=4,
            win_rate=0.6,
            total_pnl=12.5,
            total_pnl_usd=2500.0,
            avg_pnl=1.25,
            avg_pnl_usd=250.0,
            avg_win=3.0,
            avg_loss=-1.5,
            profit_factor=2.0,
            avg_r_multiple=1.25,
        )
        d = stats.to_dict()
        assert d["total_trades"] == 10
        assert d["win_rate"] == 0.6
        assert d["total_pnl"] == 12.5
        assert d["profit_factor"] == 2.0
        assert d["avg_r_multiple"] == 1.25

    def test_to_dict_infinite_profit_factor(self):
        stats = TradeStatistics(
            total_trades=1,
            open_trades=0,
            winning_trades=1,
            losing_trades=0,
            win_rate=1.0,
            total_pnl=3.0,
            total_pnl_usd=600.0,
            avg_pnl=3.0,
            avg_pnl_usd=600.0,
            avg_win=3.0,
            avg_loss=0.0,
            profit_factor=float("inf"),
            avg_r_multiple=3.0,
        )
        d = stats.to_dict()
        assert d["profit_factor"] is None


class TestTimeSeriesData:

    def test_to_dict(self):
        ts = TimeSeriesData(labels=["Jan", "Feb"], values=[10.0, 20.0])
        d = ts.to_dict()
        assert d == {"labels": ["Jan", "Feb"], "data": [10.0, 20.0]}


class TestDistributionData:

    def test_to_dict(self):
        dist = DistributionData(labels=["A", "B"], values=[5, 10])
        d = dist.to_dict()
        assert d == {"labels": ["A", "B"], "data": [5, 10]}


class TestTradeDetail:

    def test_to_dict(self):
        detail = TradeDetail(
            trade_id="T1",
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            risk_dollars=200.0,
            risk_pct=0.2,
            contracts=2.0,
            entry_time=1700000000.0,
            exit_price=130.0,
            exit_time=1700003600.0,
            result=3.0,
            result_type="TP",
            fees=4.0,
            pnl_usd=596.0,
            status="closed",
            logs=[{"event": "open", "msg": "Trade opened"}],
        )
        d = detail.to_dict()
        assert d["trade_id"] == "T1"
        assert d["pair"] == "MNQ"
        assert d["type"] == "long"
        assert d["entry"] == 100.0
        assert d["stop_loss"] == 90.0
        assert d["take_profit"] == 130.0
        assert d["risk"] == 10.0
        assert d["risk_dollars"] == 200.0
        assert d["risk_pct"] == 0.2
        assert d["contracts"] == 2.0
        assert d["entry_time"] == 1700000000.0
        assert d["exit_price"] == 130.0
        assert d["exit_time"] == 1700003600.0
        assert d["result"] == 3.0
        assert d["result_type"] == "TP"
        assert d["fees"] == 4.0
        assert d["pnl_usd"] == 596.0
        assert d["status"] == "closed"
        assert d["logs"] == [{"event": "open", "msg": "Trade opened"}]

    def test_to_dict_open_trade(self):
        detail = TradeDetail(
            trade_id="T1",
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            risk_dollars=None,
            risk_pct=None,
            contracts=None,
            entry_time=1700000000.0,
            exit_price=None,
            exit_time=None,
            result=None,
            result_type=None,
            fees=None,
            pnl_usd=None,
            status="open",
            logs=[],
        )
        d = detail.to_dict()
        assert d["status"] == "open"
        assert d["exit_price"] is None
        assert d["exit_time"] is None
        assert d["result"] is None
