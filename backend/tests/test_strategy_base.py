"""Tests for src/strategies/base_liquidity_strategy.py — line management, trade building, SL selection, exits."""

import dataclasses
from unittest.mock import MagicMock

import pytest

from src.domain.types import Direction
from src.services.trade_manager import TradeManager
from src.strategies.base_strategy import BreakevenConfig
from src.strategies.entry_context import EntryContext
from src.strategies.liquidity_v2.base_strategy import (
    BaseLiquidityStrategy,
    LineRemovalMode,
)
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from tests.conftest import make_bar
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLineRepository,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


def _make_base(event_publisher=None, line_repo=None, trade_repo=None, trade_manager=None,
               options=None, fixed_stop_loss=20, sl_levels=None, logger=None, **kwargs):
    sio = event_publisher or DummySocketIO()
    lr = line_repo or FakeLineRepository()
    tr = trade_repo or FakeTradeRepository()
    tm = trade_manager or TradeManager(
        tr, sio,
        trade_executor=FakeTradeExecutor(),
        analytics=FakeAnalyticsReporter(),
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
    )
    return BaseLiquidityStrategy(
        min_stop_loss=10.0,
        max_bounce=90.0,
        event_publisher=sio,
        line_repository=lr,
        trade_repository=tr,
        trade_manager=tm,
        extra_sl_space=0.0,
        fixed_stop_loss=fixed_stop_loss,
        options=options,
        sl_levels=sl_levels,
        rr_ratio=3.3,
        point_value=2.0,
        account_balance=100000.0,
        logger=logger or FakeLogger(),
        **kwargs,
    )


class TestLineManagement:

    def test_add_line(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=500)
        assert "L1" in strat.strategy_lines
        assert strat.strategy_lines["L1"]["level"] == 100.0
        assert strat.strategy_lines["L1"]["direction"] is None

    def test_remove_line(self):
        sio = DummySocketIO()
        lr = FakeLineRepository()
        lr.insert_line("MNQ", 100.0)
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, event_publisher=sio, line_repo=lr)
        strat.add_strategy_line("L1", 100.0)
        strat.remove_strategy_line("L1")
        assert "L1" not in strat.strategy_lines
        assert any(e[0] == "line_removed" for e in sio.events)

    def test_remove_nonexistent_line_does_not_crash(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.remove_strategy_line("DOES_NOT_EXIST")


class TestSLSelection:

    def test_tiered_picks_smallest_covering(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, sl_levels=[15, 20, 30, 40], sl_level_tolerance=3)
        # distance=17 -> 15+3=18 >= 17 -> pick 15
        assert strat._select_sl_level(17.0) == 15.0

    def test_tiered_exceeds_all_picks_largest(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, sl_levels=[15, 20, 30, 40], sl_level_tolerance=3)
        assert strat._select_sl_level(100.0) == 40.0

    def test_tiered_exact_match(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, sl_levels=[15, 20, 30, 40], sl_level_tolerance=0)
        assert strat._select_sl_level(20.0) == 20.0


class TestBuildTrade:

    def _make_ctx(self, strat, direction=Direction.LONG, close=100.0, extreme=90.0, level=100.0, bar_time=1000):
        bar = make_bar(time=bar_time, close=close, pair="MNQ")
        return EntryContext(
            strategy=strat, line_id="L1", direction=direction,
            level=level, bar=bar, close=close,
            low=bar["low"], high=bar["high"],
            extreme=extreme, cross_depth=abs(level - extreme),
        )

    def test_long_fixed_sl(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, fixed_stop_loss=20, sl_levels=None)
        ctx = self._make_ctx(strat, direction=Direction.LONG, close=100, extreme=85)
        trade = strat._build_trade_from_context(ctx)
        assert trade["type"] == "long"
        assert trade["stop_loss"] == 80.0   # 100 - 20
        assert trade["take_profit"] == pytest.approx(100 + 3.3 * 20, abs=0.1)
        assert trade["risk"] == 20.0

    def test_short_fixed_sl(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, fixed_stop_loss=20, sl_levels=None)
        ctx = self._make_ctx(strat, direction=Direction.SHORT, close=100, extreme=115)
        trade = strat._build_trade_from_context(ctx)
        assert trade["type"] == "short"
        assert trade["stop_loss"] == 120.0  # 100 + 20
        assert trade["risk"] == 20.0

    def test_tiered_sl(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, fixed_stop_loss=None, sl_levels=[15, 20, 30, 40], sl_level_tolerance=3)
        ctx = self._make_ctx(strat, direction=Direction.LONG, close=100, extreme=83)
        # distance = max(100-83, 10) = 17; pick 15 since 15+3=18>=17
        trade = strat._build_trade_from_context(ctx)
        assert trade["risk"] == 15.0

    def test_max_stop_loss_cap(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, fixed_stop_loss=None, sl_levels=None, max_stop_loss=25)
        # Dynamic risk: distance = max(100-70, 10) = 30; capped at 25
        ctx = self._make_ctx(strat, direction=Direction.LONG, close=100, extreme=70)
        trade = strat._build_trade_from_context(ctx)
        assert trade["risk"] == 25.0

    def test_trade_has_line_level(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        ctx = self._make_ctx(strat, level=105.0)
        trade = strat._build_trade_from_context(ctx)
        assert trade["line_level"] == 105.0


class TestOnTradeClosed:
    """Tests for the event-driven trade close handler (replaces _check_open_trades)."""

    def test_sl_closes_trade_and_creates_reentry(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.options.reentry_after_sl = True
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 95.0,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0, "line_level": 95.0,
            "is_reentry": False, "is_phantom": False,
        })
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["level"] == 95.0

    def test_tp_closes_trade_no_reentry(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.options.reentry_after_sl = True
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 95.0,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "TP",
            "exit_price": 130.0, "line_level": 95.0,
            "is_reentry": False, "is_phantom": False,
        })
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 0

    def test_unknown_trade_id_is_noop(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat._on_trade_closed({"trade_id": "UNKNOWN", "result_type": "SL"})
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 0

    def test_already_closed_trade_is_noop(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "closed",
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0,
            "is_reentry": False, "is_phantom": False,
        })
        assert len(strat.open_trades) == 1

    def test_reentry_trade_does_not_create_reentry(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.options.reentry_after_sl = True
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 95.0,
            "is_reentry": True,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0, "line_level": 95.0,
            "is_reentry": True, "is_phantom": False,
        })
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 0

    def test_sl_creates_reentry_when_payload_lacks_line_level(self):
        """Live: payload line_level may be None, but strategy trade still has it."""
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.options.reentry_after_sl = True
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 95.0,
            "is_reentry": False,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0,
            "line_level": None,
            "is_reentry": None,
            "is_phantom": False,
        })
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["level"] == 95.0

    def test_reentry_suppressed_when_payload_and_trade_both_flag_reentry(self):
        """If both payload and strategy trade mark it as reentry, do not chain."""
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.options.reentry_after_sl = True
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 95.0,
            "is_reentry": True,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0,
            "is_phantom": False,
        })
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 0

    def test_multi_reentry_creates_second_opportunity(self):
        """With max_reentry_attempts=2, a reentry SL creates another opportunity."""
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS))
        strat.options.reentry_after_sl = True
        strat.options.max_reentry_attempts = 2
        strat.open_trades.append({
            "trade_id": "R1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 95.0,
            "is_reentry": True, "reentry_attempt": 1,
        })
        strat._on_trade_closed({
            "trade_id": "R1", "result_type": "SL",
            "exit_price": 90.0, "line_level": 95.0,
            "is_reentry": True, "is_phantom": False,
        })
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["reentry_attempt"] == 2

    def test_multi_reentry_stops_at_max(self):
        """With max_reentry_attempts=2, the second reentry SL does NOT create a third."""
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS))
        strat.options.reentry_after_sl = True
        strat.options.max_reentry_attempts = 2
        strat.open_trades.append({
            "trade_id": "R2", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 95.0,
            "is_reentry": True, "reentry_attempt": 2,
        })
        strat._on_trade_closed({
            "trade_id": "R2", "result_type": "SL",
            "exit_price": 90.0, "line_level": 95.0,
            "is_reentry": True, "is_phantom": False,
        })
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 0


class TestPhantomExits:
    """Tests for _check_phantom_exits (strategy-only trades with no DB/broker)."""

    def test_phantom_sl_hit_closes_and_creates_reentry(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.options.reentry_after_sl = True
        strat.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 95.0,
            "is_phantom": True,
        })
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        strat._check_phantom_exits(bar)
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 1

    def test_phantom_no_hit_keeps_trade(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": True,
        })
        bar = make_bar(time=1000, low=95, high=110, pair="MNQ")
        strat._check_phantom_exits(bar)
        assert len(strat.open_trades) == 1

    def test_real_trade_ignored_by_phantom_check(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": False,
        })
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        strat._check_phantom_exits(bar)
        assert len(strat.open_trades) == 1

    def test_phantom_multi_reentry_creates_second_opportunity(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS))
        strat.options.reentry_after_sl = True
        strat.options.max_reentry_attempts = 2
        strat.open_trades.append({
            "trade_id": "phantom-R1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 95.0,
            "is_phantom": True, "is_reentry": True, "reentry_attempt": 1,
        })
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        strat._check_phantom_exits(bar)
        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["reentry_attempt"] == 2


class TestFiltersAllowEntry:

    def test_all_pass(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.entry_filters = [lambda _ctx: (True, "ok")]
        ctx = MagicMock()
        allow, reason, hold = strat._filters_allow_entry(ctx)
        assert allow is True
        assert hold is False

    def test_one_blocks(self):
        def blocker(_ctx):
            return False, "blocked"
        blocker.__name__ = "blocker"
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.entry_filters = [blocker]
        ctx = MagicMock()
        allow, reason, hold = strat._filters_allow_entry(ctx)
        assert allow is False
        assert "blocker" in reason

    def test_hold_on_block(self):
        def holder(_ctx):
            return False, "hold"
        holder.__name__ = "holder"
        holder._hold_on_block = True
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.entry_filters = [holder]
        ctx = MagicMock()
        allow, reason, hold = strat._filters_allow_entry(ctx)
        assert allow is False
        assert hold is True


class TestLineRemovalModes:

    def test_on_evaluate_removes_always(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, line_removal_mode=LineRemovalMode.ON_EVALUATE))
        strat.add_strategy_line("L1", 100.0)
        strat._maybe_remove_line("L1", opened=False)
        assert "L1" not in strat.strategy_lines

    def test_on_enter_removes_only_when_opened(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, line_removal_mode=LineRemovalMode.ON_ENTER))
        strat.add_strategy_line("L1", 100.0)
        strat._maybe_remove_line("L1", opened=False)
        assert "L1" in strat.strategy_lines
        strat._maybe_remove_line("L1", opened=True)
        assert "L1" not in strat.strategy_lines

    def test_never_keeps_line_and_resets(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, line_removal_mode=LineRemovalMode.NEVER))
        strat.add_strategy_line("L1", 100.0)
        strat.strategy_lines["L1"]["extreme"] = 50.0
        strat._maybe_remove_line("L1", opened=True)
        assert "L1" in strat.strategy_lines
        assert strat.strategy_lines["L1"]["extreme"] == 0.0


class TestBreakeven:

    def test_long_breakeven_triggered(self):
        sio = DummySocketIO()
        tr = FakeTradeRepository()
        tm = TradeManager(
            tr, sio,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        strat = _make_base(
            event_publisher=sio, trade_repo=tr, trade_manager=tm,
            options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, breakeven=BreakevenConfig(trigger_rr=2.0, move_to_rr=0.05)),
        )
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_reentry": False,
        })
        # High reaches trigger_price = 100 + 10*2 = 120
        bar = make_bar(time=1000, high=121, low=100, pair="MNQ")
        strat.check_breakeven(bar)
        # SL should move to entry + risk * 0.05 = 100.5
        assert strat.open_trades[0]["stop_loss"] == pytest.approx(100.5, abs=0.01)

    def test_breakeven_not_triggered_below_threshold(self):
        strat = _make_base(
            options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, breakeven=BreakevenConfig(trigger_rr=2.0, move_to_rr=0.05)),
        )
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_reentry": False,
        })
        bar = make_bar(time=1000, high=115, low=100, pair="MNQ")
        strat.check_breakeven(bar)
        assert strat.open_trades[0]["stop_loss"] == 90  # unchanged


class TestStoreAndEmitOpen:

    def test_persists_and_tracks(self):
        sio = DummySocketIO()
        tr = FakeTradeRepository()
        tm = TradeManager(
            tr, sio,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, event_publisher=sio, trade_repo=tr, trade_manager=tm)
        trade = {
            "pair": "MNQ", "type": "long", "entry": 100,
            "stop_loss": 90, "take_profit": 130, "risk": 10,
            "entry_time": 1000, "status": "open",
        }
        strat._store_and_emit_open(trade)
        assert trade["trade_id"] is not None
        assert len(strat.open_trades) == 1
        assert len(tr.inserted) == 1
        open_events = [e for e in sio.events if e[0] == "trade_open"]
        assert len(open_events) == 1
