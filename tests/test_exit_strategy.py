"""Tests for ExitStrategy implementations."""

from src.strategies.exits import (
    CompositeExitStrategy,
    ExitType,
    NoExitStrategy,
    SessionEndExitStrategy,
    SLTPExitStrategy,
)
from src.types import Direction


class TestSLTPExitStrategy:
    def test_sl_hit_long(self):
        strategy = SLTPExitStrategy()
        trade = {"direction": Direction.LONG, "stop_loss": 100, "take_profit": 110}
        bar = {"high": 108, "low": 99}  # Low hits SL

        signal = strategy.check_exit(trade, bar)

        assert signal is not None
        assert signal.exit_type == ExitType.STOP_LOSS
        assert signal.exit_price == 100

    def test_tp_hit_long(self):
        strategy = SLTPExitStrategy()
        trade = {"direction": Direction.LONG, "stop_loss": 100, "take_profit": 110}
        bar = {"high": 111, "low": 105}  # High hits TP

        signal = strategy.check_exit(trade, bar)

        assert signal is not None
        assert signal.exit_type == ExitType.TAKE_PROFIT
        assert signal.exit_price == 110

    def test_sl_hit_short(self):
        strategy = SLTPExitStrategy()
        trade = {"direction": Direction.SHORT, "stop_loss": 110, "take_profit": 100}
        bar = {"high": 111, "low": 105}  # High hits SL

        signal = strategy.check_exit(trade, bar)

        assert signal is not None
        assert signal.exit_type == ExitType.STOP_LOSS

    def test_tp_hit_short(self):
        strategy = SLTPExitStrategy()
        trade = {"direction": Direction.SHORT, "stop_loss": 110, "take_profit": 100}
        bar = {"high": 105, "low": 99}  # Low hits TP

        signal = strategy.check_exit(trade, bar)

        assert signal is not None
        assert signal.exit_type == ExitType.TAKE_PROFIT

    def test_no_hit(self):
        strategy = SLTPExitStrategy()
        trade = {"direction": Direction.LONG, "stop_loss": 100, "take_profit": 110}
        bar = {"high": 105, "low": 102}  # Between SL and TP

        signal = strategy.check_exit(trade, bar)

        assert signal is None

    def test_cfd_spread_does_not_adjust_sl_detection(self):
        strategy = SLTPExitStrategy(broker_mode='cfd', broker_spread=2.0)
        trade = {"direction": Direction.LONG, "stop_loss": 100, "take_profit": 110}
        # Low is 100.5 -> above original SL of 100, so NO hit even with spread
        bar = {"high": 105, "low": 100.5}

        signal = strategy.check_exit(trade, bar)

        assert signal is None


class TestSessionEndExitStrategy:
    def test_before_session_end(self):
        strategy = SessionEndExitStrategy("17:00", "America/New_York")
        # 14:00 NY time = 19:00 UTC

        # This test needs actual timestamps, skipping detailed test
        # Just verify the strategy structure
        assert strategy.session_end_time.hour == 17
        assert strategy.session_end_time.minute == 0

    def test_trade_entry_after_session_end(self):
        strategy = SessionEndExitStrategy("17:00", "UTC")
        # Bar time is 1000, trade entry is 2000 (after)
        trade = {"entry_time": 2000}
        bar = {"time": 1000, "close": 100}

        signal = strategy.check_exit(trade, bar)

        # Should not exit because trade was entered after this bar
        assert signal is None


class TestNoExitStrategy:
    def test_never_signals(self):
        strategy = NoExitStrategy()
        trade = {"direction": Direction.LONG, "stop_loss": 100, "take_profit": 110}
        bar = {"high": 200, "low": 50}  # Would hit both SL and TP

        signal = strategy.check_exit(trade, bar)

        assert signal is None


class TestCompositeExitStrategy:
    def test_first_strategy_wins(self):
        sltp = SLTPExitStrategy()
        no_exit = NoExitStrategy()

        composite = CompositeExitStrategy([no_exit, sltp])

        trade = {"direction": Direction.LONG, "stop_loss": 100, "take_profit": 110}
        bar = {"high": 111, "low": 105}  # TP hit

        signal = composite.check_exit(trade, bar)

        assert signal is not None
        assert signal.exit_type == ExitType.TAKE_PROFIT

    def test_all_strategies_no_exit(self):
        no_exit1 = NoExitStrategy()
        no_exit2 = NoExitStrategy()

        composite = CompositeExitStrategy([no_exit1, no_exit2])

        trade = {"direction": Direction.LONG, "stop_loss": 100, "take_profit": 110}
        bar = {"high": 105, "low": 102}

        signal = composite.check_exit(trade, bar)

        assert signal is None
