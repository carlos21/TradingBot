"""Integration tests: per-instrument open-trades cap + trading windows.

Exercises the real ``StrategyOptions.entry_filters`` chain assembled by
``get_prod_strategy_options`` for each instrument, with fabricated entry
contexts (fake strategy state, bar times placed inside/outside the London
01:00-07:59 and NY 08:00-15:30 windows).

Semantics under test:
- ``open_trades_limit`` = one global concurrent-open cap (catalog
  ``max_open_trades`` = 2), across all windows, initial entries and re-entries.
- ``trading_windows`` = window membership + per-window INITIAL-entry limit
  (``max_trades`` = 1 per window) + per-window concurrent-open limit
  (``max_open_trades`` = 1), where an open trade is attributed to the window
  instance in which its CURRENT leg was opened. Two concurrent trades are
  therefore only possible when their active legs live in DIFFERENT windows.

June 2025 = EDT (UTC-4): 14:00 UTC is 10:00 New York.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.domain.models import TradeData
from src.strategies.entry_context import EntryContext
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from src.strategies.liquidity_v2.prod_config import (
    get_prod_strategy_numbers,
    get_prod_strategy_options,
)

REENTRY_FILTER_NAMES = {"time_range", "open_trades_limit", "rollover", "trading_windows"}

LONDON_BAR = int(datetime(2025, 6, 15, 10, 0, tzinfo=timezone.utc).timestamp())   # 06:00 NY
NY_BAR = int(datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc).timestamp())       # 10:00 NY
OUTSIDE_BAR = int(datetime(2025, 6, 15, 22, 0, tzinfo=timezone.utc).timestamp())  # 18:00 NY
NY_LEG = int(datetime(2025, 6, 15, 12, 30, tzinfo=timezone.utc).timestamp())      # 08:30 NY


def _options(symbol: str):
    numbers = get_prod_strategy_numbers(rr_ratio=5.0, symbol=symbol)
    return get_prod_strategy_options(
        numbers.max_bounce,
        numbers.min_cross_depth,
        skip_rollover_days=False,
        reentry_only=False,
        line_removal_mode=DEFAULT_STRATEGY_OPTIONS.line_removal_mode,
        max_reentry_attempts=1,
        symbol=symbol,
    )


def _repo_trade(entry_time, source=None, params=None):
    return TradeData(
        trade_id="T1", pair="MNQ", trade_type="long",
        entry_price=100, stop_loss=90, take_profit=130, risk=10,
        risk_dollars=None, risk_pct=None, account_balance=None, contracts=None,
        entry_time=entry_time,
        exit_price=None, exit_time=None, result=None, result_type=None,
        fees=None, pnl_usd=None, params=params, source=source,
    )


def _strategy(open_count=0, open_sources=None, repo_trades=None):
    sources = open_sources or [None] * open_count
    strategy = MagicMock()
    strategy.open_trades = [{"status": "open", "source": s} for s in sources]
    strategy.trade_repository = MagicMock()
    strategy.trade_repository.list_trades.return_value = repo_trades or []
    return strategy


def _strategy_with_open_legs(leg_entries, repo_trades=None):
    """Strategy with open trades whose CURRENT leg opened at the given epochs."""
    strategy = MagicMock()
    strategy.open_trades = [
        {"status": "open", "source": None, "entry_time": e} for e in leg_entries
    ]
    strategy.trade_repository = MagicMock()
    strategy.trade_repository.list_trades.return_value = repo_trades or []
    return strategy


def _ctx(strategy, bar_time, cross_depth=10.0, is_reentry=False):
    bar = {"time": bar_time, "pair": "MNQ", "open": 100, "high": 105, "low": 95, "close": 101}
    return EntryContext(
        strategy=strategy,
        line_id="L1",
        direction="long",
        level=100.0,
        bar=bar,
        close=bar["close"],
        low=bar["low"],
        high=bar["high"],
        extreme=90.0,
        cross_depth=cross_depth,
        is_reentry=is_reentry,
    )


def _run_entry_filters(options, ctx):
    """Mirror BaseLiquidityStrategy._filters_allow_entry."""
    for f in options.entry_filters:
        ok, reason = f(ctx)
        if not ok:
            return False, f.name, reason
    return True, None, None


def _run_reentry_filters(options, ctx):
    """Mirror BaseLiquidityStrategy._filters_allow_reentry."""
    for f in options.entry_filters:
        if f.__name__ not in REENTRY_FILTER_NAMES:
            continue
        ok, reason = f(ctx)
        if not ok:
            return False, f.name, reason
    return True, None, None


class TestEntryChainOpenTradesCap:
    def test_i1_london_setup_with_no_open_trades_allowed(self):
        ok, name, reason = _run_entry_filters(_options("MNQ"), _ctx(_strategy(0), LONDON_BAR))
        assert ok is True, f"blocked by {name}: {reason}"

    def test_i2_ny_setup_allowed_with_london_trade_still_open(self):
        """Headline case: London carryover + NY setup -> 2 concurrent trades."""
        ok, name, reason = _run_entry_filters(_options("MNQ"), _ctx(_strategy(1), NY_BAR))
        assert ok is True, f"blocked by {name}: {reason}"

    def test_i3_third_setup_blocked_when_two_open(self):
        for bar_time, label in ((LONDON_BAR, "london"), (NY_BAR, "ny")):
            ok, name, reason = _run_entry_filters(_options("MNQ"), _ctx(_strategy(2), bar_time))
            assert ok is False, f"{label}: expected block"
            assert name == "open_trades_limit", f"{label}: blocked by {name}"

    def test_i4_reentry_allowed_with_one_open_blocked_with_two(self):
        options = _options("MNQ")
        ok, name, reason = _run_reentry_filters(options, _ctx(_strategy(1), NY_BAR, is_reentry=True))
        assert ok is True, f"blocked by {name}: {reason}"

        ok, name, reason = _run_reentry_filters(options, _ctx(_strategy(2), NY_BAR, is_reentry=True))
        assert ok is False
        assert name == "open_trades_limit"

    def test_i4b_reentry_trades_do_not_consume_window_slots(self):
        """An initial entry is allowed in a window whose only trade is a re-entry."""
        repo = [_repo_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc),  # 09:00 NY
                            params={"is_reentry": True, "reentry_attempt": 1})]
        ok, name, reason = _run_entry_filters(_options("MNQ"), _ctx(_strategy(0, repo_trades=repo), NY_BAR))
        assert ok is True, f"blocked by {name}: {reason}"

    def test_i5_second_initial_in_same_window_blocked_by_window_limit(self):
        """Blocked by trading_windows max_trades=1, not by the open-trades cap."""
        repo = [_repo_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc))]  # 09:00 NY
        ok, name, reason = _run_entry_filters(
            _options("MNQ"), _ctx(_strategy(1, repo_trades=repo), NY_BAR))
        assert ok is False
        assert name == "trading_windows", f"blocked by {name}: {reason}"
        assert "limit reached" in reason

    def test_i6_setup_outside_all_windows_blocked_with_open_slot(self):
        ok, name, reason = _run_entry_filters(_options("MNQ"), _ctx(_strategy(0), OUTSIDE_BAR))
        assert ok is False
        assert name == "trading_windows"
        assert "outside all trading windows" in reason

    def test_i7_manual_open_trade_counts_toward_cap_but_not_window_limit(self):
        # Global cap: manual open trades count (concurrency is source-agnostic).
        strategy = _strategy(open_sources=["manual", "strategy"])
        ok, name, _ = _run_entry_filters(_options("MNQ"), _ctx(strategy, NY_BAR))
        assert ok is False
        assert name == "open_trades_limit"

        # Window max_trades: manual entries are excluded from the count.
        repo = [_repo_trade(datetime(2025, 6, 15, 13, 0, tzinfo=timezone.utc), source="manual")]
        ok, name, reason = _run_entry_filters(
            _options("MNQ"), _ctx(_strategy(0, repo_trades=repo), NY_BAR))
        assert ok is True, f"blocked by {name}: {reason}"

    def test_i8_entry_allowed_again_after_one_trade_closes(self):
        options = _options("MNQ")
        ok, _, _ = _run_entry_filters(options, _ctx(_strategy(2), NY_BAR))
        assert ok is False
        # One trade closes -> back below the cap.
        ok, name, reason = _run_entry_filters(options, _ctx(_strategy(1), NY_BAR))
        assert ok is True, f"blocked by {name}: {reason}"

    def test_i9_mes_options_enforce_mes_cap(self):
        options = _options("MES")
        # MES cross-depth bounds: 1.5 <= depth <= 30.
        ok, name, reason = _run_entry_filters(options, _ctx(_strategy(1), NY_BAR, cross_depth=5.0))
        assert ok is True, f"blocked by {name}: {reason}"
        ok, name, _ = _run_entry_filters(options, _ctx(_strategy(2), NY_BAR, cross_depth=5.0))
        assert ok is False
        assert name == "open_trades_limit"


class TestSameWindowConcurrency:
    """Per-window concurrent-open limit: an open trade is attributed to the
    window instance in which its CURRENT leg was opened, so two concurrent
    trades are only possible across DIFFERENT session windows."""

    def test_ny_setup_blocked_with_ny_window_leg_open(self):
        """Regression (scenario 2024-10-21): a London-born chain's re-entry
        leg opened at 08:30 NY, then a NY initial triggered while that leg
        was still open — two concurrent NY-session trades. Must be blocked
        by trading_windows (the global cap of 2 alone allowed it)."""
        strategy = _strategy_with_open_legs([NY_LEG])
        ok, name, reason = _run_entry_filters(_options("MNQ"), _ctx(strategy, NY_BAR))
        assert ok is False
        assert name == "trading_windows", f"blocked by {name}: {reason}"
        assert "open-trades" in reason

    def test_ny_setup_allowed_with_london_window_leg_open(self):
        """The intended feature: a trade whose active leg lives in the London
        window does not consume the NY window's concurrent-open slot."""
        strategy = _strategy_with_open_legs([LONDON_BAR])
        ok, name, reason = _run_entry_filters(_options("MNQ"), _ctx(strategy, NY_BAR))
        assert ok is True, f"blocked by {name}: {reason}"

    def test_reentry_blocked_with_same_window_leg_open(self):
        """A re-entry evaluated inside a window that already has an open leg
        is blocked as well (mirrors the pre-d135f53 behavior)."""
        strategy = _strategy_with_open_legs([NY_LEG])
        ok, name, reason = _run_reentry_filters(
            _options("MNQ"), _ctx(strategy, NY_BAR, is_reentry=True))
        assert ok is False
        assert name == "trading_windows", f"blocked by {name}: {reason}"
