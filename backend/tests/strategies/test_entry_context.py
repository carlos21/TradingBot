"""Tests for src.strategies.entry_context."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.domain.types import Direction
from src.strategies.entry_context import (
    PAIR_TIMEZONES,
    EntryContext,
    daily_trades_limit_filter,
    max_bounce_filter,
    min_cross_depth_filter,
    open_trades_limit_filter,
    rollover_filter,
    time_range_filter,
)


class FakeTrade:
    def __init__(self, source="strategy", entry_time=None):
        self.source = source
        self.entry_time = entry_time or datetime.now(timezone.utc)


class FakeTradeRepository:
    def __init__(self, trades=None):
        self._trades = trades or []

    def list_trades(self, pair: str):
        return self._trades


class FakeStrategy:
    def __init__(self, open_trades=None, repo=None):
        self.open_trades = open_trades or []
        self.trade_repository = repo or FakeTradeRepository()


def _make_ctx(strategy=None, direction=Direction.LONG, bar=None, cross_depth=5.0, extreme=95.0):
    bar = bar or {"time": 1700000000, "pair": "MNQ"}
    return EntryContext(
        strategy=strategy or FakeStrategy(),
        line_id="L1",
        direction=direction,
        level=100.0,
        bar=bar,
        close=bar.get("close", 100.0),
        low=bar.get("low", 99.0),
        high=bar.get("high", 101.0),
        extreme=extreme,
        cross_depth=cross_depth,
    )


class TestEntryContext:

    def test_is_long(self):
        ctx = _make_ctx(direction=Direction.LONG)
        assert ctx.is_long is True
        assert ctx.is_short is False

    def test_is_short(self):
        ctx = _make_ctx(direction=Direction.SHORT)
        assert ctx.is_short is True
        assert ctx.is_long is False


class TestOpenTradesLimitFilter:

    def test_limit_none_allows(self):
        f = open_trades_limit_filter(None)
        ctx = _make_ctx(FakeStrategy(open_trades=[{"status": "open"}]))
        allowed, reason = f(ctx)
        assert allowed is True

    def test_below_limit_allows(self):
        f = open_trades_limit_filter(2)
        ctx = _make_ctx(FakeStrategy(open_trades=[{"status": "open"}]))
        allowed, reason = f(ctx)
        assert allowed is True

    def test_at_limit_blocks(self):
        f = open_trades_limit_filter(1)
        ctx = _make_ctx(FakeStrategy(open_trades=[{"status": "open"}]))
        allowed, reason = f(ctx)
        assert allowed is False
        assert "open-trades 1 >= limit 1" in reason

    def test_closed_trades_ignored(self):
        f = open_trades_limit_filter(1)
        ctx = _make_ctx(FakeStrategy(open_trades=[{"status": "closed"}]))
        allowed, reason = f(ctx)
        assert allowed is True


class TestMinCrossDepthFilter:

    def test_depth_met_allows(self):
        f = min_cross_depth_filter(5.0)
        ctx = _make_ctx(cross_depth=6.0)
        allowed, reason = f(ctx)
        assert allowed is True

    def test_depth_not_met_blocks(self):
        f = min_cross_depth_filter(5.0)
        ctx = _make_ctx(cross_depth=3.0)
        allowed, reason = f(ctx)
        assert allowed is False
        assert "depth=3.00 < min_depth=5.0" in reason

    def test_sets_hold_on_block(self):
        f = min_cross_depth_filter(5.0)
        assert f.hold_on_block is True


class TestMaxBounceFilter:

    def test_bounce_within_limit_allows(self):
        f = max_bounce_filter(10.0)
        ctx = _make_ctx(cross_depth=8.0)
        allowed, reason = f(ctx)
        assert allowed is True

    def test_bounce_exceeds_limit_blocks(self):
        f = max_bounce_filter(10.0)
        ctx = _make_ctx(cross_depth=12.0)
        allowed, reason = f(ctx)
        assert allowed is False
        assert "depth=12.00 > max_bounce=10.0" in reason


class TestTimeRangeFilter:

    def test_inside_range_allows(self):
        f = time_range_filter("08:00", "17:00", "America/New_York")
        # 13:00 UTC is 08:00 EST (standard time) / 09:00 EDT depending on date
        ctx = _make_ctx(bar={"time": datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc).timestamp(), "pair": "MNQ"})
        allowed, _ = f(ctx)
        assert allowed is True

    def test_outside_range_blocks(self):
        f = time_range_filter("08:00", "17:00", "America/New_York")
        # 23:00 UTC is 18:00 EST in January, outside 08:00-17:00
        ctx = _make_ctx(bar={"time": datetime(2024, 1, 15, 23, 0, tzinfo=timezone.utc).timestamp(), "pair": "MNQ"})
        allowed, reason = f(ctx)
        assert allowed is False
        assert "outside" in reason

    def test_auto_timezone_resolution(self):
        f = time_range_filter("08:00", "17:00")
        ctx = _make_ctx(bar={"time": datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc).timestamp(), "pair": "MNQ"})
        allowed, _ = f(ctx)
        assert allowed is True

    def test_unknown_pair_defaults_to_utc(self):
        f = time_range_filter("08:00", "17:00")
        ctx = _make_ctx(bar={"time": datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc).timestamp(), "pair": "UNKNOWN"})
        allowed, _ = f(ctx)
        assert allowed is True

    def test_overnight_range(self):
        f = time_range_filter("22:00", "02:00", "America/New_York")
        # 03:00 UTC is 22:00 EST previous day
        ctx = _make_ctx(bar={"time": datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc).timestamp(), "pair": "MNQ"})
        allowed, _ = f(ctx)
        assert allowed is True


class TestRolloverFilter:

    def test_disabled_allows(self):
        f = rollover_filter(enabled=False)
        ctx = _make_ctx()
        allowed, _ = f(ctx)
        assert allowed is True

    def test_non_rollover_day_allows(self):
        f = rollover_filter(enabled=True, timezone_str="America/New_York")
        ctx = _make_ctx(bar={"time": datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc).timestamp(), "pair": "MNQ"})
        allowed, _ = f(ctx)
        assert allowed is True

    def test_rollover_day_blocks(self):
        # March 2024 rollover: 3rd Friday is 15th, rollover is Thursday 7th.
        f = rollover_filter(enabled=True, timezone_str="America/New_York")
        ctx = _make_ctx(bar={"time": datetime(2024, 3, 7, 14, 0, tzinfo=timezone.utc).timestamp(), "pair": "MNQ"})
        allowed, reason = f(ctx)
        assert allowed is False
        assert "Rollover day" in reason

    def test_cache_reuse(self):
        f = rollover_filter(enabled=True, timezone_str="America/New_York")
        ctx = _make_ctx(bar={"time": datetime(2024, 3, 7, 14, 0, tzinfo=timezone.utc).timestamp(), "pair": "MNQ"})
        f(ctx)
        # Second call should hit cache but still block
        allowed, _ = f(ctx)
        assert allowed is False


class TestDailyTradesLimitFilter:

    def test_no_trades_allows(self):
        f = daily_trades_limit_filter(5)
        ctx = _make_ctx(FakeStrategy(repo=FakeTradeRepository([])))
        allowed, _ = f(ctx)
        assert allowed is True

    def test_below_limit_allows(self):
        bar_time = datetime(2023, 11, 14, 22, 0, tzinfo=timezone.utc)
        trades = [FakeTrade(entry_time=bar_time)]
        f = daily_trades_limit_filter(2)
        ctx = _make_ctx(FakeStrategy(repo=FakeTradeRepository(trades)))
        allowed, _ = f(ctx)
        assert allowed is True

    def test_at_limit_blocks(self):
        bar_time = datetime(2023, 11, 14, 22, 0, tzinfo=timezone.utc)
        trades = [
            FakeTrade(entry_time=bar_time),
            FakeTrade(entry_time=bar_time + timedelta(hours=1)),
        ]
        f = daily_trades_limit_filter(2)
        ctx = _make_ctx(FakeStrategy(repo=FakeTradeRepository(trades)))
        allowed, reason = f(ctx)
        assert allowed is False
        assert "Daily limit reached" in reason

    def test_manual_and_test_sources_excluded(self):
        bar_time = datetime(2023, 11, 14, 22, 0, tzinfo=timezone.utc)
        trades = [
            FakeTrade(source="manual", entry_time=bar_time),
            FakeTrade(source="test", entry_time=bar_time),
        ]
        f = daily_trades_limit_filter(1)
        ctx = _make_ctx(FakeStrategy(repo=FakeTradeRepository(trades)))
        allowed, _ = f(ctx)
        assert allowed is True

    def test_naive_entry_time_handled(self):
        bar_time = datetime(2023, 11, 14, 22, 0)  # naive
        trades = [FakeTrade(entry_time=bar_time)]
        f = daily_trades_limit_filter(1)
        ctx = _make_ctx(FakeStrategy(repo=FakeTradeRepository(trades)))
        allowed, _ = f(ctx)
        assert allowed is False


class TestPairTimezones:

    def test_mnq_maps_to_new_york(self):
        assert PAIR_TIMEZONES["MNQ"] == "America/New_York"

    def test_eurusd_maps_to_new_york(self):
        assert PAIR_TIMEZONES["EURUSD"] == "America/New_York"
