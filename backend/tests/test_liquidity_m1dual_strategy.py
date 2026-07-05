"""Tests for src/strategies/liquidity_m1dual_strategy.py."""

import pytest

from src.strategies.liquidity_m1dual.strategy import LiquidityDualM1Strategy
from tests.fakes import FakeLineRepository, FakeLogger, FakeTradeRepository, MutableTradingContext


def _make_socketio():
    class SocketIO:
        def __init__(self):
            self.emitted = []
        def emit(self, event, payload):
            self.emitted.append((event, payload))
    return SocketIO()


def _make_strategy(warmup: bool = False):
    return LiquidityDualM1Strategy(
        min_stop_loss=10.0,
        max_bounce=50.0,
        event_publisher=_make_socketio(),
        line_repository=FakeLineRepository(),
        trade_repository=FakeTradeRepository(),
        extra_sl_space={"MNQ": 0.0},
        logger=FakeLogger(),
        point_value=2.0,
        fee_per_rt=1.5,
        execution_context=MutableTradingContext(warmup=warmup),
    )


@pytest.fixture
def strategy():
    return _make_strategy()


class TestLiquidityDualM1StrategyInit:

    def test_initial_state(self, strategy):
        assert strategy.min_stop_loss == 10.0
        assert strategy.max_bounce == 50.0
        assert strategy.strategy_lines == {}
        assert strategy.open_trades == []


class TestLiquidityDualM1StrategyLineManagement:

    def test_add_strategy_line(self, strategy):
        strategy.add_strategy_line("L1", 5000.0, "long")
        assert "L1" in strategy.strategy_lines
        assert strategy.strategy_lines["L1"]["level"] == 5000.0
        assert strategy.strategy_lines["L1"]["direction"] == "long"
        assert strategy.strategy_lines["L1"]["crosses"] == 0
        assert strategy.strategy_lines["L1"]["extreme"] == float("inf")

    def test_add_strategy_line_short(self, strategy):
        strategy.add_strategy_line("L1", 5000.0, "short")
        assert strategy.strategy_lines["L1"]["extreme"] == float("-inf")

    def test_remove_strategy_line(self, strategy):
        strategy.add_strategy_line("L1", 5000.0, "long")
        strategy.remove_strategy_line("L1")
        assert "L1" not in strategy.strategy_lines


class TestLiquidityDualM1StrategyWarmup:

    def test_warmup_skips_processing(self):
        strategy = _make_strategy(warmup=True)
        strategy.add_strategy_line("L1", 100.0, "long")
        bar = {"open": 105.0, "high": 106.0, "low": 95.0, "close": 95.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar)
        # Should not process due to warmup
        assert strategy.strategy_lines["L1"]["crosses"] == 0


class TestLiquidityDualM1StrategyLongLogic:

    def test_first_cross_down(self, strategy):
        strategy.add_strategy_line("L1", 100.0, "long")
        bar = {"open": 105.0, "high": 106.0, "low": 95.0, "close": 95.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar)
        assert strategy.strategy_lines["L1"]["crosses"] == 1
        assert strategy.strategy_lines["L1"]["extreme"] == 95.0

    def test_bounce_tracking(self, strategy):
        strategy.add_strategy_line("L1", 100.0, "long")
        # First cross
        bar1 = {"open": 105.0, "high": 106.0, "low": 95.0, "close": 95.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar1)
        # Lower low
        bar2 = {"open": 96.0, "high": 97.0, "low": 90.0, "close": 91.0, "time": 2000, "pair": "MNQ"}
        strategy.on_raw_bar(bar2)
        assert strategy.strategy_lines["L1"]["extreme"] == 90.0

    def test_entry_cross_opens_trade(self, strategy):
        strategy.add_strategy_line("L1", 100.0, "long")
        # First cross
        bar1 = {"open": 105.0, "high": 106.0, "low": 95.0, "close": 95.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar1)
        # Entry cross
        bar2 = {"open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0, "time": 2000, "pair": "MNQ"}
        strategy.on_raw_bar(bar2)
        # Line should be removed after entry
        assert "L1" not in strategy.strategy_lines
        # Trade should be open
        assert len(strategy.open_trades) == 1
        assert strategy.open_trades[0]["type"] == "long"

    def test_entry_cross_depth_too_large(self, strategy):
        strategy.max_bounce = 3.0  # Very small max bounce
        strategy.add_strategy_line("L1", 100.0, "long")
        # First cross with large drop
        bar1 = {"open": 105.0, "high": 106.0, "low": 90.0, "close": 90.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar1)
        # Entry cross — depth = 100 - 90 = 10 > max_bounce = 3
        bar2 = {"open": 95.0, "high": 102.0, "low": 89.0, "close": 101.0, "time": 2000, "pair": "MNQ"}
        strategy.on_raw_bar(bar2)
        # Line removed but no trade opened
        assert "L1" not in strategy.strategy_lines
        assert len(strategy.open_trades) == 0

    def test_no_entry_when_open_trade_exists(self, strategy):
        strategy.add_strategy_line("L1", 100.0, "long")
        # First cross
        bar1 = {"open": 105.0, "high": 106.0, "low": 95.0, "close": 95.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar1)
        # Entry cross
        bar2 = {"open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0, "time": 2000, "pair": "MNQ"}
        strategy.on_raw_bar(bar2)
        initial_count = len(strategy.trade_repository.inserted)
        # Add another line and try to trigger it — but open trade blocks new entries
        strategy.add_strategy_line("L2", 200.0, "long")
        bar3 = {"open": 205.0, "high": 206.0, "low": 195.0, "close": 195.0, "time": 3000, "pair": "MNQ"}
        strategy.on_raw_bar(bar3)
        # No new trade should be inserted because open trade exists
        assert len(strategy.trade_repository.inserted) == initial_count


class TestLiquidityDualM1StrategyShortLogic:

    def test_first_cross_up(self, strategy):
        strategy.add_strategy_line("L1", 100.0, "short")
        bar = {"open": 95.0, "high": 106.0, "low": 94.0, "close": 105.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar)
        assert strategy.strategy_lines["L1"]["crosses"] == 1
        assert strategy.strategy_lines["L1"]["extreme"] == 106.0

    def test_entry_cross_opens_short_trade(self, strategy):
        strategy.add_strategy_line("L1", 100.0, "short")
        # First cross up
        bar1 = {"open": 95.0, "high": 106.0, "low": 94.0, "close": 105.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar1)
        # Entry cross down
        bar2 = {"open": 102.0, "high": 103.0, "low": 97.0, "close": 99.0, "time": 2000, "pair": "MNQ"}
        strategy.on_raw_bar(bar2)
        assert "L1" not in strategy.strategy_lines
        assert len(strategy.open_trades) == 1
        assert strategy.open_trades[0]["type"] == "short"


class TestLiquidityDualM1StrategyCheckOpenTrades:

    def test_sl_hit_closes_long(self, strategy):
        strategy.add_strategy_line("L1", 100.0, "long")
        bar1 = {"open": 105.0, "high": 106.0, "low": 95.0, "close": 95.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar1)
        bar2 = {"open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0, "time": 2000, "pair": "MNQ"}
        strategy.on_raw_bar(bar2)
        assert len(strategy.open_trades) == 1
        sl = strategy.open_trades[0]["stop_loss"]
        # Bar that hits SL
        bar3 = {"open": 102.0, "high": 103.0, "low": sl - 1, "close": sl, "time": 3000, "pair": "MNQ"}
        strategy.on_raw_bar(bar3)
        # Trade should be removed from open_trades when closed
        assert len(strategy.open_trades) == 0

    def test_tp_hit_closes_long(self, strategy):
        strategy.add_strategy_line("L1", 100.0, "long")
        bar1 = {"open": 105.0, "high": 106.0, "low": 95.0, "close": 95.0, "time": 1000, "pair": "MNQ"}
        strategy.on_raw_bar(bar1)
        bar2 = {"open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0, "time": 2000, "pair": "MNQ"}
        strategy.on_raw_bar(bar2)
        assert len(strategy.open_trades) == 1
        tp = strategy.open_trades[0]["take_profit"]
        # Bar that hits TP
        bar3 = {"open": tp - 1, "high": tp + 1, "low": tp - 2, "close": tp, "time": 3000, "pair": "MNQ"}
        strategy.on_raw_bar(bar3)
        # Trade should be removed from open_trades when closed
        assert len(strategy.open_trades) == 0
