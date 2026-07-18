"""Tests for entry filters in src/strategies/entry_context.py."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.domain.models import TradeData
from src.strategies.entry_context import (
    EntryContext,
    TradingWindow,
    daily_trades_limit_filter,
    max_bounce_filter,
    min_cross_depth_filter,
    open_trades_limit_filter,
    rollover_filter,
    time_range_filter,
    trading_windows_filter,
)


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

    def test_allows_overnight_range(self):
        f = time_range_filter("22:00", "02:00", "America/New_York")
        # 23:00 NY
        bar_time = int(datetime(2025, 6, 15, 3, 0, tzinfo=timezone.utc).timestamp())
        ctx = _make_ctx(bar_time=bar_time, pair="MNQ")
        ok, _ = f(ctx)
        assert ok is True

    def test_blocks_overnight_range(self):
        f = time_range_filter("22:00", "02:00", "America/New_York")
        # 14:00 NY - outside 22:00-02:00
        bar_time = int(datetime(2025, 6, 15, 18, 0, tzinfo=timezone.utc).timestamp())
        ctx = _make_ctx(bar_time=bar_time, pair="MNQ")
        ok, _ = f(ctx)
        assert ok is False


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
                risk_dollars=None, risk_pct=None, account_balance=None, contracts=None,
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
                risk_dollars=None, risk_pct=None, account_balance=None, contracts=None,
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
                risk_dollars=None, risk_pct=None, account_balance=None, contracts=None,
                entry_time=datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None, source="test",
            ),
            TradeData(
                trade_id="T2", pair="MNQ", trade_type="long",
                entry_price=100, stop_loss=90, take_profit=130, risk=10,
                risk_dollars=None, risk_pct=None, account_balance=None, contracts=None,
                entry_time=datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None, source="manual",
            ),
            TradeData(
                trade_id="T3", pair="MNQ", trade_type="long",
                entry_price=100, stop_loss=90, take_profit=130, risk=10,
                risk_dollars=None, risk_pct=None, account_balance=None, contracts=None,
                entry_time=datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None, source="manual",
            ),
        ]
        ctx = _make_ctx(strategy=strategy, bar_time=bar_time)
        ok, reason = f(ctx)
        assert ok is True, f"Expected allow but got: {reason}"
        assert "daily_count 0" in reason

    def test_auto_detects_pair_timezone(self):
        f = daily_trades_limit_filter(1)
        bar_time = int(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc).timestamp())
        strategy = MagicMock()
        strategy.open_trades = []
        strategy.trade_repository = MagicMock()
        strategy.trade_repository.list_trades.return_value = [
            TradeData(
                trade_id="T1", pair="MNQ", trade_type="long",
                entry_price=100, stop_loss=90, take_profit=130, risk=10,
                risk_dollars=None, risk_pct=None, account_balance=None, contracts=None,
                entry_time=datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None,
            )
        ]
        ctx = _make_ctx(strategy=strategy, bar_time=bar_time)
        ok, reason = f(ctx)
        assert ok is False
        assert "Daily limit" in reason

    def test_handles_naive_entry_time(self):
        f = daily_trades_limit_filter(1, "America/New_York")
        bar_time = int(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc).timestamp())
        strategy = MagicMock()
        strategy.open_trades = []
        strategy.trade_repository = MagicMock()
        strategy.trade_repository.list_trades.return_value = [
            TradeData(
                trade_id="T1", pair="MNQ", trade_type="long",
                entry_price=100, stop_loss=90, take_profit=130, risk=10,
                risk_dollars=None, risk_pct=None, account_balance=None, contracts=None,
                entry_time=datetime(2025, 6, 15, 13, 0),  # naive
                exit_price=None, exit_time=None, result=None, result_type=None,
                fees=None, pnl_usd=None, params=None,
            )
        ]
        ctx = _make_ctx(strategy=strategy, bar_time=bar_time)
        ok, reason = f(ctx)
        assert ok is False
        assert "Daily limit" in reason


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


# ----------------------------------------------------------------------
# Trading windows filter
# ----------------------------------------------------------------------

_NY = "America/New_York"


def _window_strategy(open_trades=None, trades=None):
    strategy = MagicMock()
    strategy.open_trades = open_trades or []
    strategy.trade_repository = MagicMock()
    strategy.trade_repository.list_trades.return_value = trades or []
    return strategy


def _window_trade(entry_time, source=None, params=None):
    return TradeData(
        trade_id="T1", pair="MNQ", trade_type="long",
        entry_price=100, stop_loss=90, take_profit=130, risk=10,
        risk_dollars=None, risk_pct=None, account_balance=None, contracts=None,
        entry_time=entry_time,
        exit_price=None, exit_time=None, result=None, result_type=None,
        fees=None, pnl_usd=None, params=params, source=source,
    )


class TestTradingWindowsFilter:
    """June 2025 = EDT (UTC-4): 14:00 UTC is 10:00 New York."""

    def test_allows_inside_window(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30")], _NY)
        ok, _ = f(_make_ctx())
        assert ok is True

    def test_blocks_outside_all_windows(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30")], _NY)
        bar_time = int(datetime(2025, 6, 15, 22, 0, tzinfo=timezone.utc).timestamp())  # 18:00 NY
        ok, reason = f(_make_ctx(bar_time=bar_time))
        assert ok is False
        assert "outside all trading windows" in reason

    def test_blocks_second_initial_entry_in_window(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30", max_trades=1)], _NY)
        strategy = _window_strategy(trades=[
            _window_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc)),  # 09:00 NY
        ])
        ok, reason = f(_make_ctx(strategy=strategy))
        assert ok is False
        assert "limit reached" in reason

    def test_allows_when_prior_trade_in_other_window(self):
        f = trading_windows_filter([
            TradingWindow("08:00", "11:00", max_trades=1),
            TradingWindow("13:30", "15:30", max_trades=1),
        ], _NY)
        strategy = _window_strategy(trades=[
            _window_trade(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc)),  # 10:00 NY, window 1
        ])
        # 18:00 UTC = 14:00 NY -> window 2
        bar_time = int(datetime(2025, 6, 15, 18, 0, tzinfo=timezone.utc).timestamp())
        ok, reason = f(_make_ctx(strategy=strategy, bar_time=bar_time))
        assert ok is True, f"Expected allow but got: {reason}"

    def test_reentry_trades_do_not_consume_slots(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30", max_trades=1)], _NY)
        strategy = _window_strategy(trades=[
            _window_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                          params={"is_reentry": True, "reentry_attempt": 1}),
        ])
        ok, reason = f(_make_ctx(strategy=strategy))
        assert ok is True, f"Expected allow but got: {reason}"

    def test_reentry_attempt_alone_counts_as_reentry(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30", max_trades=1)], _NY)
        strategy = _window_strategy(trades=[
            _window_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),
                          params={"reentry_attempt": 2}),
        ])
        ok, reason = f(_make_ctx(strategy=strategy))
        assert ok is True, f"Expected allow but got: {reason}"

    def test_reentry_ctx_bypasses_max_trades(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30", max_trades=1)], _NY)
        strategy = _window_strategy(trades=[
            _window_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc)),
        ])
        ctx = _make_ctx(strategy=strategy)
        ctx.is_reentry = True
        ok, reason = f(ctx)
        assert ok is True, f"Expected allow but got: {reason}"

    def test_reentry_ctx_blocked_outside_window(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30")], _NY)
        bar_time = int(datetime(2025, 6, 15, 22, 0, tzinfo=timezone.utc).timestamp())  # 18:00 NY
        ctx = _make_ctx(bar_time=bar_time)
        ctx.is_reentry = True
        ok, reason = f(ctx)
        assert ok is False
        assert "outside all trading windows" in reason

    def test_reentry_ctx_respects_open_trades_limit(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30", max_open_trades=1)], _NY)
        strategy = _window_strategy(open_trades=[{"status": "open"}])
        ctx = _make_ctx(strategy=strategy)
        ctx.is_reentry = True
        ok, reason = f(ctx)
        assert ok is False
        assert "open-trades" in reason

    def test_open_trades_limit_per_window(self):
        f = trading_windows_filter([
            TradingWindow("08:00", "11:00", max_open_trades=1),
            TradingWindow("13:30", "15:30", max_open_trades=2),
        ], _NY)
        # 14:00 NY (window 2, limit 2) with 1 open trade -> allowed
        strategy = _window_strategy(open_trades=[{"status": "open"}])
        bar_time = int(datetime(2025, 6, 15, 18, 0, tzinfo=timezone.utc).timestamp())
        ok, _ = f(_make_ctx(strategy=strategy, bar_time=bar_time))
        assert ok is True
        # 10:00 NY (window 1, limit 1) with 1 open trade -> blocked
        ok, reason = f(_make_ctx(strategy=strategy))
        assert ok is False
        assert "open-trades" in reason

    def test_blocks_in_gap_between_windows(self):
        f = trading_windows_filter([
            TradingWindow("08:00", "11:00"),
            TradingWindow("13:30", "15:30"),
        ], _NY)
        bar_time = int(datetime(2025, 6, 15, 16, 0, tzinfo=timezone.utc).timestamp())  # 12:00 NY
        ok, reason = f(_make_ctx(bar_time=bar_time))
        assert ok is False
        assert "outside all trading windows" in reason

    def test_overnight_window_allows_both_sides_of_midnight(self):
        f = trading_windows_filter([TradingWindow("22:00", "02:00")], _NY)
        # 2025-06-16 02:30 UTC = 2025-06-15 22:30 NY
        ok, _ = f(_make_ctx(bar_time=int(datetime(2025, 6, 16, 2, 30, tzinfo=timezone.utc).timestamp())))
        assert ok is True
        # 2025-06-16 05:00 UTC = 2025-06-16 01:00 NY
        ok, _ = f(_make_ctx(bar_time=int(datetime(2025, 6, 16, 5, 0, tzinfo=timezone.utc).timestamp())))
        assert ok is True

    def test_overnight_window_blocks_outside(self):
        f = trading_windows_filter([TradingWindow("22:00", "02:00")], _NY)
        bar_time = int(datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc).timestamp())  # 08:00 NY
        ok, _ = f(_make_ctx(bar_time=bar_time))
        assert ok is False

    def test_overnight_window_counts_post_midnight_trade(self):
        f = trading_windows_filter([TradingWindow("22:00", "02:00", max_trades=1)], _NY)
        strategy = _window_strategy(trades=[
            # 2025-06-16 03:00 UTC = 2025-06-15 23:00 NY -> same instance as the bar
            _window_trade(datetime(2025, 6, 16, 3, 0, tzinfo=timezone.utc)),
        ])
        # 2025-06-16 05:00 UTC = 2025-06-16 01:00 NY
        bar_time = int(datetime(2025, 6, 16, 5, 0, tzinfo=timezone.utc).timestamp())
        ok, reason = f(_make_ctx(strategy=strategy, bar_time=bar_time))
        assert ok is False
        assert "limit reached" in reason

    def test_auto_detects_pair_timezone(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30", max_trades=1)])
        strategy = _window_strategy(trades=[
            _window_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc)),  # 09:00 NY
        ])
        ok, reason = f(_make_ctx(strategy=strategy))  # pair="MNQ" -> America/New_York
        assert ok is False
        assert "limit reached" in reason

    def test_ignores_manual_and_test_trades(self):
        f = trading_windows_filter([TradingWindow("08:00", "15:30", max_trades=1)], _NY)
        strategy = _window_strategy(trades=[
            _window_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc), source="manual"),
            _window_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc), source="test"),
        ])
        ok, reason = f(_make_ctx(strategy=strategy))
        assert ok is True, f"Expected allow but got: {reason}"
