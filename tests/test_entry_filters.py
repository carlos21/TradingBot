"""Tests for entry filters in src/strategies/entry_context.py."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.strategies.entry_context import (
    EntryContext,
    open_trades_limit_filter,
    min_cross_depth_filter,
    max_bounce_filter,
    time_range_filter,
    daily_trades_limit_filter,
    rollover_filter,
)
from src.models import TradeData


def _make_ctx(strategy=None, bar_time=None, pair="MNQ", cross_depth=10.0, level=100.0, extreme=90.0):
    if strategy is None:
        strategy = MagicMock()
        strategy.open_trades = []
        strategy.trade_repository = MagicMock()
        strategy.trade_repository.list_trades.return_value = []
    if bar_time is None:
        # 2025-06-15 10:00 NY (within trading hours)
        bar_time = int(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc).timestamp())
    bar = {"time": bar_time, "pair": pair, "open": 100, "high": 105, "low": 95, "close": 101}
    return EntryContext(
        strategy=strategy,
        line_id="L1",
        direction="long",
        level=level,
        bar=bar,
        close=bar["close"],
        low=bar["low"],
        high=bar["high"],
        extreme=extreme,
        cross_depth=cross_depth,
    )


class TestOpenTradesLimitFilter:

    def test_allows_when_no_open_trades(self):
        f = open_trades_limit_filter(1)
        ctx = _make_ctx()
        ok, _ = f(ctx)
        assert ok is True

    def test_blocks_when_limit_reached(self):
        f = open_trades_limit_filter(1)
        strategy = MagicMock()
        strategy.open_trades = [{"status": "open"}]
        strategy.trade_repository = MagicMock()
        strategy.trade_repository.list_trades.return_value = []
        ctx = _make_ctx(strategy=strategy)
        ok, reason = f(ctx)
        assert ok is False
        assert "limit" in reason

    def test_unlimited_always_allows(self):
        f = open_trades_limit_filter(None)
        strategy = MagicMock()
        strategy.open_trades = [{"status": "open"}] * 10
        ctx = _make_ctx(strategy=strategy)
        ok, _ = f(ctx)
        assert ok is True

    def test_ignores_closed_trades(self):
        f = open_trades_limit_filter(1)
        strategy = MagicMock()
        strategy.open_trades = [{"status": "closed"}]
        strategy.trade_repository = MagicMock()
        strategy.trade_repository.list_trades.return_value = []
        ctx = _make_ctx(strategy=strategy)
        ok, _ = f(ctx)
        assert ok is True


class TestMinCrossDepthFilter:

    def test_allows_sufficient_depth(self):
        f = min_cross_depth_filter(5.0)
        ctx = _make_ctx(cross_depth=10.0)
        ok, _ = f(ctx)
        assert ok is True

    def test_blocks_insufficient_depth(self):
        f = min_cross_depth_filter(15.0)
        ctx = _make_ctx(cross_depth=10.0)
        ok, _ = f(ctx)
        assert ok is False

    def test_has_hold_on_block(self):
        f = min_cross_depth_filter(5.0)
        assert getattr(f, "_hold_on_block", False) is True


class TestMaxBounceFilter:

    def test_allows_within_bounce(self):
        f = max_bounce_filter(50.0)
        ctx = _make_ctx(cross_depth=30.0)
        ok, _ = f(ctx)
        assert ok is True

    def test_blocks_over_bounce(self):
        f = max_bounce_filter(20.0)
        ctx = _make_ctx(cross_depth=30.0)
        ok, _ = f(ctx)
        assert ok is False

    def test_exact_boundary_allowed(self):
        f = max_bounce_filter(10.0)
        ctx = _make_ctx(cross_depth=10.0)
        ok, _ = f(ctx)
        assert ok is True


class TestTimeRangeFilter:

    def test_allows_within_range(self):
        f = time_range_filter("08:00", "17:00", "America/New_York")
        # 10:00 AM NY
        bar_time = int(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc).timestamp())
        ctx = _make_ctx(bar_time=bar_time)
        ok, _ = f(ctx)
        assert ok is True

    def test_blocks_outside_range(self):
        f = time_range_filter("08:00", "14:00", "America/New_York")
        # 6:00 AM NY = outside range
        bar_time = int(datetime(2025, 6, 15, 10, 0, tzinfo=timezone.utc).timestamp())
        ctx = _make_ctx(bar_time=bar_time)
        ok, _ = f(ctx)
        assert ok is False

    def test_auto_detects_pair_timezone(self):
        f = time_range_filter("08:00", "17:00")
        # 10:00 AM NY
        bar_time = int(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc).timestamp())
        ctx = _make_ctx(bar_time=bar_time, pair="MNQ")
        ok, _ = f(ctx)
        assert ok is True


class TestDailyTradesLimitFilter:

    def test_allows_when_no_trades_today(self):
        f = daily_trades_limit_filter(1, "America/New_York")
        strategy = MagicMock()
        strategy.open_trades = []
        strategy.trade_repository = MagicMock()
        strategy.trade_repository.list_trades.return_value = []
        ctx = _make_ctx(strategy=strategy)
        ok, _ = f(ctx)
        assert ok is True

    def test_blocks_when_daily_limit_reached(self):
        f = daily_trades_limit_filter(1, "America/New_York")
        bar_time = int(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc).timestamp())
        strategy = MagicMock()
        strategy.open_trades = []
        strategy.trade_repository = MagicMock()
        strategy.trade_repository.list_trades.return_value = [
            TradeData(
                trade_id="T1", pair="MNQ", trade_type="long",
                entry_price=100, stop_loss=90, take_profit=130, risk=10,
                risk_dollars=None, risk_pct=None, contracts=None,
                entry_time=datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None,
            )
        ]
        ctx = _make_ctx(strategy=strategy, bar_time=bar_time)
        ok, reason = f(ctx)
        assert ok is False
        assert "Daily limit" in reason

    def test_allows_when_trades_on_different_day(self):
        f = daily_trades_limit_filter(1, "America/New_York")
        bar_time = int(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc).timestamp())
        strategy = MagicMock()
        strategy.open_trades = []
        strategy.trade_repository = MagicMock()
        strategy.trade_repository.list_trades.return_value = [
            TradeData(
                trade_id="T1", pair="MNQ", trade_type="long",
                entry_price=100, stop_loss=90, take_profit=130, risk=10,
                risk_dollars=None, risk_pct=None, contracts=None,
                entry_time=datetime(2025, 6, 14, 14, 0, tzinfo=timezone.utc),
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None,
            )
        ]
        ctx = _make_ctx(strategy=strategy, bar_time=bar_time)
        ok, _ = f(ctx)
        assert ok is True

    def test_ignores_manual_and_test_trades(self):
        f = daily_trades_limit_filter(1, "America/New_York")
        bar_time = int(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc).timestamp())
        strategy = MagicMock()
        strategy.open_trades = []
        strategy.trade_repository = MagicMock()
        strategy.trade_repository.list_trades.return_value = [
            TradeData(
                trade_id="T1", pair="MNQ", trade_type="long",
                entry_price=100, stop_loss=90, take_profit=130, risk=10,
                risk_dollars=None, risk_pct=None, contracts=None,
                entry_time=datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None, source="test",
            ),
            TradeData(
                trade_id="T2", pair="MNQ", trade_type="long",
                entry_price=100, stop_loss=90, take_profit=130, risk=10,
                risk_dollars=None, risk_pct=None, contracts=None,
                entry_time=datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None, source="manual",
            ),
            TradeData(
                trade_id="T3", pair="MNQ", trade_type="long",
                entry_price=100, stop_loss=90, take_profit=130, risk=10,
                risk_dollars=None, risk_pct=None, contracts=None,
                entry_time=datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None, source="broker_sync",
            ),
        ]
        ctx = _make_ctx(strategy=strategy, bar_time=bar_time)
        ok, reason = f(ctx)
        assert ok is True, f"Expected allow but got: {reason}"
        assert "daily_count 0" in reason


class TestRolloverFilter:

    def test_disabled_always_allows(self):
        f = rollover_filter(enabled=False)
        ctx = _make_ctx()
        ok, reason = f(ctx)
        assert ok is True
        assert "disabled" in reason

    def test_blocks_on_rollover_day(self):
        # 2025-03-13 is the Thursday 8 days before 3rd Friday (March 21).
        f = rollover_filter(enabled=True, timezone_str="America/New_York")
        bar_time = int(datetime(2025, 3, 13, 14, 0, tzinfo=timezone.utc).timestamp())
        ctx = _make_ctx(bar_time=bar_time)
        ok, _ = f(ctx)
        assert ok is False

    def test_allows_on_non_rollover_day(self):
        f = rollover_filter(enabled=True, timezone_str="America/New_York")
        # June 15, 2025 is a Sunday — not a rollover day
        bar_time = int(datetime(2025, 6, 16, 14, 0, tzinfo=timezone.utc).timestamp())
        ctx = _make_ctx(bar_time=bar_time)
        ok, _ = f(ctx)
        assert ok is True
