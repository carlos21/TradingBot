"""Expanded coverage for src/strategies/base_liquidity_strategy.py.

Focus areas:
- BE threshold logic
- Session end handling edge cases
- Multi-account expansion
- Trailing SL
- Edge cases in trade management
- Methods not well covered by test_strategy_base.py
"""

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

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
    MutableTradingContext,
)


def _make_base(event_publisher=None, line_repo=None, trade_repo=None, trade_manager=None,
               options=None, fixed_stop_loss=20, sl_levels=None, logger=None,
               account_configs=None, broker_spread=0.0, use_fractional_lots=False,
               risk_pct_per_trade=None, account_balance=100000.0,
               execution_context=None, **kwargs):
    sio = event_publisher or DummySocketIO()
    lr = line_repo or FakeLineRepository()
    tr = trade_repo or FakeTradeRepository()
    tm = trade_manager or TradeManager(
        tr, sio,
        trade_executor=FakeTradeExecutor(),
        analytics=FakeAnalyticsReporter(),
        point_value=2.0,
        account_balance=account_balance,
        logger=FakeLogger(),
    )
    # Accept plain dicts for account_configs in tests
    if account_configs:
        account_configs = [
            SimpleNamespace(
                name=ac.get("name"),
                risk_usd=ac.get("risk_usd"),
                risk_pct=ac.get("risk_pct"),
                rr_ratio=ac.get("rr_ratio"),
            )
            if isinstance(ac, dict) else ac
            for ac in account_configs
        ]
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
        account_balance=account_balance,
        logger=logger or FakeLogger(),
        account_configs=account_configs,
        broker_spread=broker_spread,
        use_fractional_lots=use_fractional_lots,
        risk_pct_per_trade=risk_pct_per_trade,
        execution_context=execution_context or MutableTradingContext(warmup=False, trading_enabled=True),
        **kwargs,
    )


def _make_ctx(strat, direction=Direction.LONG, close=100.0, extreme=90.0, level=100.0, bar_time=1000):
    bar = make_bar(time=bar_time, close=close, pair="MNQ")
    return EntryContext(
        strategy=strat, line_id="L1", direction=direction,
        level=level, bar=bar, close=close,
        low=bar["low"], high=bar["high"],
        extreme=extreme, cross_depth=abs(level - extreme),
    )


# ---------------------------------------------------------------------------
# BE Threshold Logic
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Multi-Account Expansion
# ---------------------------------------------------------------------------

class TestMultiAccountExpansion:

    def test_store_and_emit_open_creates_independent_account_trade(self):
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
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS,
            event_publisher=sio, trade_repo=tr, trade_manager=tm,
            account_configs=[{"name": "A1"}],
        )
        trade = {
            "pair": "MNQ", "type": "long", "entry": 100,
            "stop_loss": 90, "take_profit": 130, "risk": 10,
            "entry_time": 1000, "status": "open", "line_level": 100,
        }
        strat._store_and_emit_open(trade)
        assert trade["trade_id"] is not None
        assert trade["account"] == "A1"
        assert len(tr.inserted) == 1
        assert tr.inserted[0]["source"] == "strategy"
        assert tr.inserted[0]["params"]["is_reentry"] is False
        open_events = [e for e in sio.events if e[0] == "trade_open"]
        assert len(open_events) == 1

    def test_restore_open_trades_multi_account(self):
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
        # Seed trade_manager with independent account trades
        tm.open_trades.append({
            "trade_id": "AT1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
            "entry_time": 500,
        })
        tm.open_trades.append({
            "trade_id": "AT2", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
            "entry_time": 500,
        })
        strat.restore_open_trades()
        assert len(strat.open_trades) == 2
        assert {t["trade_id"] for t in strat.open_trades} == {"AT1", "AT2"}

    def test_restore_open_trades_single_account(self):
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
        tm.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
        })
        strat.restore_open_trades()
        assert len(strat.open_trades) == 1
        assert strat.open_trades[0]["trade_id"] == "T1"

    def test_restore_open_trades_skips_if_already_populated(self):
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
        strat.open_trades.append({"trade_id": "X1", "status": "open"})
        tm.open_trades.append({"trade_id": "T1", "status": "open"})
        strat.restore_open_trades()
        assert len(strat.open_trades) == 1
        assert strat.open_trades[0]["trade_id"] == "X1"


# ---------------------------------------------------------------------------
# Trailing SL
# ---------------------------------------------------------------------------

class TestTrailingSL:

    def test_update_trade_sl_updates_all_layers(self):
        sio = DummySocketIO()
        tr = FakeTradeRepository()
        executor = FakeTradeExecutor()
        tm = TradeManager(
            tr, sio,
            trade_executor=executor,
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, event_publisher=sio, trade_repo=tr, trade_manager=tm)
        trade = {
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
        }
        strat.open_trades.append(trade)
        tm.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "entry_time": 500,
        })
        # Insert into repo so update_stop_loss finds it
        tr.inserted.append({"trade_id": "T1", "stop_loss": 90})

        strat._update_trade_sl(trade, 95.0)
        assert trade["stop_loss"] == 95.0
        assert executor.sl_updates == [("T1", 95.0)]
        update_events = [e for e in sio.events if e[0] == "trade_update"]
        assert len(update_events) == 1
        assert update_events[0][1]["stop_loss"] == 95.0

    def test_short_breakeven_triggered(self):
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
            "trade_id": "T1", "pair": "MNQ", "type": "short",
            "entry": 100, "stop_loss": 110, "take_profit": 70,
            "risk": 10, "status": "open", "is_reentry": False,
        })
        # trigger_price = 100 - 10*2 = 80
        bar = make_bar(time=1000, high=100, low=79, pair="MNQ")
        strat.check_breakeven(bar)
        # proposed_sl = 100 - 10*0.05 = 99.5, which is < current_sl=110
        assert strat.open_trades[0]["stop_loss"] == pytest.approx(99.5, abs=0.01)

    def test_breakeven_warmup_skips(self):
        strat = _make_base(
            options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, breakeven=BreakevenConfig(trigger_rr=2.0, move_to_rr=0.05)),
        )
        strat.execution_context.warmup = True
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_reentry": False,
        })
        bar = make_bar(time=1000, high=121, low=100, pair="MNQ")
        strat.check_breakeven(bar)
        assert strat.open_trades[0]["stop_loss"] == 90

    def test_reentry_breakeven_uses_separate_config(self):
        strat = _make_base(
            options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS,
                breakeven=None,
                reentry_breakeven=BreakevenConfig(trigger_rr=1.5, move_to_rr=0.0),
            ),
        )
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_reentry": True,
        })
        bar = make_bar(time=1000, high=115, low=100, pair="MNQ")
        strat.check_breakeven(bar)
        # trigger = 100 + 10*1.5 = 115, high=115 >= 115 → move SL to entry
        assert strat.open_trades[0]["stop_loss"] == pytest.approx(100.0, abs=0.01)


# ---------------------------------------------------------------------------
# Edge Cases in Trade Management
# ---------------------------------------------------------------------------

class TestEdgeCasesTradeManagement:

    def test_phantom_trade_not_persisted(self):
        tr = FakeTradeRepository()
        strat = _make_base(trade_repo=tr, options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_only=True))
        strat.open_trades.append({
            "trade_id": "phantom-1000", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": True,
        })
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        strat._check_phantom_exits(bar)
        assert len(strat.open_trades) == 0
        # Phantom close should NOT be persisted
        phantom_closes = [c for c in tr.closed if c["trade_id"] == "phantom-1000"]
        assert len(phantom_closes) == 0

    def test_sl_event_creates_reentry_opportunity(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 100,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0, "line_level": 100,
            "is_reentry": False, "is_phantom": False,
        })
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["level"] == 100
        assert strat._reentry_opportunities[0]["direction"] == "long"

    def test_sl_event_creates_reentry_with_trade_id(self):
        """Independent trades: payload trade_id matches strategy trade."""
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 100,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0, "line_level": 100,
            "is_reentry": False, "is_phantom": False,
        })
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["level"] == 100
        assert strat._reentry_opportunities[0]["direction"] == "long"
        assert len(strat.open_trades) == 0

    def test_trade_updated_syncs_entry_fill(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 100,
        })
        strat._on_trade_updated({
            "trade_id": "T1",
            "entry_price": 99.5, "stop_loss": 89.5,
            "take_profit": 131.0, "risk": 10.5, "contracts": 2,
        })
        trade = strat.open_trades[0]
        assert trade["entry"] == 99.5
        assert trade["stop_loss"] == 89.5
        assert trade["take_profit"] == 131.0
        assert trade["risk"] == 10.5
        assert trade["contracts"] == 2

    def test_trade_entry_updated_event_syncs_entry_fill(self):
        """Regression: TRADE_ENTRY_UPDATED must update strategy trade dict."""
        from src.domain.events import DomainEvent, EventType
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 100,
        })
        strat.on_event(DomainEvent(
            EventType.TRADE_ENTRY_UPDATED,
            payload={
                "trade_id": "T1",
                "entry_price": 99.5, "stop_loss": 89.5,
                "take_profit": 131.0, "risk": 10.5, "contracts": 2,
            },
        ))
        trade = strat.open_trades[0]
        assert trade["entry"] == 99.5
        assert trade["stop_loss"] == 89.5
        assert trade["take_profit"] == 131.0
        assert trade["risk"] == 10.5
        assert trade["contracts"] == 2

    def test_tp_event_does_not_create_reentry(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 100,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "TP",
            "exit_price": 130.0, "line_level": 100,
            "is_reentry": False, "is_phantom": False,
        })
        assert len(strat._reentry_opportunities) == 0

    def test_phantom_sl_hit_creates_reentry(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_only=True))
        strat.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 100, "is_phantom": True,
        })
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        strat._check_phantom_exits(bar)
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["level"] == 100

    def test_calc_contracts_fractional_lots(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, use_fractional_lots=True, risk_pct_per_trade=1.0)
        # risk_budget = 100000 * 1% = 1000
        # risk_per_contract = 20 * 2 = 40
        # lots = 1000 / 40 = 25.0
        assert strat._calc_contracts(40.0) == 25.0

    def test_calc_contracts_zero_risk_per_contract(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, use_fractional_lots=False)
        assert strat._calc_contracts(0.0) == 1.0
        strat_frac = _make_base(options=DEFAULT_STRATEGY_OPTIONS, use_fractional_lots=True)
        assert strat_frac._calc_contracts(0.0) == 0.01

    def test_calc_contracts_uses_trade_manager_balance(self):
        tm = TradeManager(
            FakeTradeRepository(), DummySocketIO(),
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=50000.0,
            logger=FakeLogger(),
        )
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, trade_manager=tm, risk_pct_per_trade=1.0)
        # risk_budget = 50000 * 1% = 500
        assert strat._calc_contracts(40.0) == 13  # 500/40 = 12.5 → round-half-up = 13

    def test_make_trade_dict_risk_pct_none_when_zero_balance(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, account_balance=0.0)
        bar = make_bar(time=1000, pair="MNQ")
        trade = strat._make_trade_dict(bar, "long", 100, 90, 130, 10)
        assert trade["risk_pct"] is None

    def test_on_raw_bar_aggregation(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.strategy_window = 300  # 5m = 300s for easier testing
        processed = []
        strat._on_strategy_bar = lambda bar: processed.append(bar)

        # First bar at t=0
        strat.on_raw_bar(make_bar(time=0, close=100, pair="MNQ"))
        assert len(processed) == 0
        # Second bar at t=300 (new window)
        strat.on_raw_bar(make_bar(time=300, close=101, pair="MNQ"))
        assert len(processed) == 1
        assert processed[0]["close"] == 100

    def test_breakeven_called_after_trade_manager_in_pipeline(self):
        """Breakeven is no longer inside on_raw_bar; it runs after TradeManager
        in the backtest callback pipeline. Verify _check_breakeven works when
        called explicitly."""
        strat = _make_base(
            options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, breakeven=BreakevenConfig(trigger_rr=1.0, move_to_rr=0.0)),
        )
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_reentry": False,
        })
        bar = make_bar(time=0, close=110, high=110, low=110, pair="MNQ")
        strat.check_breakeven(bar)
        assert strat.open_trades[0]["stop_loss"] == 100.0  # BE moved

    def test_invalid_strategy_tf_raises(self):
        with pytest.raises(ValueError):
            BaseLiquidityStrategy(
                min_stop_loss=10.0,
                max_bounce=90.0,
                event_publisher=DummySocketIO(),
                line_repository=FakeLineRepository(),
                trade_repository=FakeTradeRepository(),
                trade_manager=TradeManager(
                    FakeTradeRepository(), DummySocketIO(),
                    trade_executor=FakeTradeExecutor(),
                    analytics=FakeAnalyticsReporter(),
                    point_value=2.0,
                    account_balance=100000.0,
                    logger=FakeLogger(),
                ),
                extra_sl_space=0.0,
                point_value=2.0,
                account_balance=100000.0,
                strategy_tf="",
                logger=FakeLogger(),
            )


# ---------------------------------------------------------------------------
# Re-Entry Opportunities
# ---------------------------------------------------------------------------

class TestReentryOpportunities:

    def test_long_reentry_triggered(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "long", "pair": "MNQ",
            "extreme_excursion": 100.0,
        })
        bar = make_bar(time=1000, close=101, high=102, low=99, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        assert len(strat._reentry_opportunities) == 0
        assert len(strat.open_trades) == 1
        assert strat.open_trades[0].get("is_reentry") is True

    def test_long_reentry_cancelled_by_adverse_excursion(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True, reentry_threshold=10.0))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "long", "pair": "MNQ",
            "extreme_excursion": 100.0,
        })
        bar = make_bar(time=1000, close=99, high=100, low=89, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        assert len(strat._reentry_opportunities) == 0
        assert len(strat.open_trades) == 0

    def test_short_reentry_triggered(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "short", "pair": "MNQ",
            "extreme_excursion": 100.0,
        })
        bar = make_bar(time=1000, close=99, high=101, low=98, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        assert len(strat._reentry_opportunities) == 0
        assert len(strat.open_trades) == 1
        assert strat.open_trades[0].get("is_reentry") is True

    def test_short_reentry_cancelled_by_adverse_excursion(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True, reentry_threshold=10.0))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "short", "pair": "MNQ",
            "extreme_excursion": 100.0,
        })
        bar = make_bar(time=1000, close=101, high=111, low=100, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        assert len(strat._reentry_opportunities) == 0
        assert len(strat.open_trades) == 0

    def test_reentry_blocked_when_trade_open(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "long", "pair": "MNQ",
            "extreme_excursion": 100.0,
        })
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
        })
        bar = make_bar(time=1000, close=101, high=102, low=99, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        # Opportunity stays alive because a trade is already open
        assert len(strat._reentry_opportunities) == 1

    def test_reentry_warmup_skips(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.execution_context.warmup = True
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "long", "pair": "MNQ",
            "extreme_excursion": 100.0,
        })
        bar = make_bar(time=1000, close=101, high=102, low=99, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        assert len(strat._reentry_opportunities) == 1

    def test_reentry_different_pair_ignored(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "long", "pair": "MES",
            "extreme_excursion": 100.0,
        })
        bar = make_bar(time=1000, close=101, high=102, low=99, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        assert len(strat._reentry_opportunities) == 1

    def test_reentry_skipped_on_same_bar_as_sl(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "long", "pair": "MNQ",
            "extreme_excursion": 100.0,
            "sl_bar_time": 1000,
        })
        bar = make_bar(time=1000, close=101, high=102, low=99, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        # Same bar as SL — opportunity stays alive
        assert len(strat._reentry_opportunities) == 1
        assert len(strat.open_trades) == 0

    def test_reentry_long_requires_bullish_candle(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "long", "pair": "MNQ",
            "extreme_excursion": 100.0,
            "sl_bar_time": 999,
        })
        # Bearish candle: close < open, but close > level
        bar = make_bar(time=1000, open_=105, close=101, high=106, low=99, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        # Not bullish — opportunity stays alive
        assert len(strat._reentry_opportunities) == 1
        assert len(strat.open_trades) == 0

    def test_reentry_short_requires_bearish_candle(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "short", "pair": "MNQ",
            "extreme_excursion": 100.0,
            "sl_bar_time": 999,
        })
        # Bullish candle: close > open, but close < level
        bar = make_bar(time=1000, open_=95, close=99, high=101, low=94, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        # Not bearish — opportunity stays alive
        assert len(strat._reentry_opportunities) == 1
        assert len(strat.open_trades) == 0

    def test_reentry_trade_tracks_attempt_number(self):
        """When a reentry fires, the trade should record its attempt number."""
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat._reentry_opportunities.append({
            "level": 100.0, "direction": "long", "pair": "MNQ",
            "extreme_excursion": 100.0, "reentry_attempt": 2,
        })
        bar = make_bar(time=1000, close=101, high=102, low=99, pair="MNQ")
        strat._check_reentry_opportunities(bar)
        assert len(strat._reentry_opportunities) == 0
        assert len(strat.open_trades) == 1
        assert strat.open_trades[0].get("is_reentry") is True
        assert strat.open_trades[0].get("reentry_attempt") == 2

    def test_reentry_second_attempt_creates_opportunity_on_sl(self):
        """With max_reentry_attempts=3, a reentry attempt 2 hitting SL creates attempt 3."""
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True, max_reentry_attempts=3))
        strat.open_trades.append({
            "trade_id": "R2", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 100.0,
            "is_reentry": True, "reentry_attempt": 2,
        })
        strat._on_trade_closed({
            "trade_id": "R2", "result_type": "SL",
            "exit_price": 90.0, "line_level": 100.0,
            "is_reentry": True, "is_phantom": False,
        })
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["reentry_attempt"] == 3


# ---------------------------------------------------------------------------
# Restore & Persist
# ---------------------------------------------------------------------------

class TestRestoreAndPersist:

    def test_restore_trigger_states_overlays_correctly(self):
        from src.infrastructure.repositories.line_trigger_state_repository import (
            InMemoryLineTriggerStateRepository,
        )
        repo = InMemoryLineTriggerStateRepository()
        repo.save("L1", "MNQ", {"direction": "long", "extreme": 50.0, "extra": "x"})
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, trigger_state_repo=repo)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=500)
        strat.restore_trigger_states("MNQ")
        assert strat.strategy_lines["L1"]["direction"] == "long"
        assert strat.strategy_lines["L1"]["extreme"] == 50.0
        assert strat.strategy_lines["L1"]["extra"] == "x"
        # Level and creation_ts should remain from bootstrap
        assert strat.strategy_lines["L1"]["level"] == 100.0
        assert strat.strategy_lines["L1"]["creation_ts"] == 500.0

    def test_persist_all_line_states_writes_to_repo(self):
        from src.infrastructure.repositories.line_trigger_state_repository import (
            InMemoryLineTriggerStateRepository,
        )
        repo = InMemoryLineTriggerStateRepository()
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, trigger_state_repo=repo)
        strat.add_strategy_line("L1", 100.0)
        strat.strategy_lines["L1"]["direction"] = "long"
        strat._persist_all_line_states()
        saved = repo.load_all("MNQ")
        assert "L1" in saved
        assert saved["L1"]["direction"] == "long"

    def test_restore_reentry_opportunities_from_db(self):
        from datetime import timedelta
        tr = FakeTradeRepository()
        now = datetime.now(timezone.utc)
        # Insert a recent losing trade with line_level
        tr.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=now - timedelta(minutes=30),
            params={"line_level": 100.0},
        )
        # Close it with negative result to simulate SL hit
        trade_data = tr.inserted[-1]
        tr.close_trade(
            trade_id=trade_data["trade_id"],
            exit_price=90,
            exit_time=now - timedelta(minutes=25),
            result=-1.0,
            result_type="SL",
        )
        strat = _make_base(trade_repo=tr, options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.restore_reentry_opportunities("MNQ", reference_time=now)
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["level"] == 100.0

    def test_restore_reentry_opportunities_skips_if_disabled(self):
        tr = FakeTradeRepository()
        strat = _make_base(trade_repo=tr, options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=False, reentry_only=False))
        strat.restore_reentry_opportunities("MNQ")
        assert len(strat._reentry_opportunities) == 0

    def test_restore_reentry_opportunities_dedupes(self):
        from datetime import timedelta
        tr = FakeTradeRepository()
        now = datetime.now(timezone.utc)
        for _ in range(2):
            tr.insert_trade(
                pair="MNQ", trade_type="long", entry_price=100,
                stop_loss=90, take_profit=130, risk=10,
                entry_time=now - timedelta(minutes=30),
                params={"line_level": 100.0},
            )
            td = tr.inserted[-1]
            tr.close_trade(td["trade_id"], 90, now - timedelta(minutes=25), -1.0, "SL")
        strat = _make_base(trade_repo=tr, options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.restore_reentry_opportunities("MNQ", reference_time=now)
        assert len(strat._reentry_opportunities) == 1

    def test_restore_reentry_opportunities_multi_attempt(self):
        """Restore should create opportunity for reentry attempt 2 when max=3."""
        from datetime import timedelta
        tr = FakeTradeRepository()
        now = datetime.now(timezone.utc)
        tr.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=now - timedelta(minutes=30),
            params={"line_level": 100.0, "is_reentry": True, "reentry_attempt": 1},
        )
        td = tr.inserted[-1]
        tr.close_trade(td["trade_id"], 90, now - timedelta(minutes=25), -1.0, "SL")
        strat = _make_base(trade_repo=tr, options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True, max_reentry_attempts=3))
        strat.restore_reentry_opportunities("MNQ", reference_time=now)
        assert len(strat._reentry_opportunities) == 1
        assert strat._reentry_opportunities[0]["reentry_attempt"] == 2

    def test_restore_reentry_opportunities_stops_at_max(self):
        """Restore should NOT create opportunity when next_attempt > max."""
        from datetime import timedelta
        tr = FakeTradeRepository()
        now = datetime.now(timezone.utc)
        tr.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=now - timedelta(minutes=30),
            params={"line_level": 100.0, "is_reentry": True, "reentry_attempt": 2},
        )
        td = tr.inserted[-1]
        tr.close_trade(td["trade_id"], 90, now - timedelta(minutes=25), -1.0, "SL")
        strat = _make_base(trade_repo=tr, options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True, max_reentry_attempts=2))
        strat.restore_reentry_opportunities("MNQ", reference_time=now)
        assert len(strat._reentry_opportunities) == 0


# ---------------------------------------------------------------------------
# Broker Exit Fill
# ---------------------------------------------------------------------------

class TestTradeClosedEvent:
    """Strategy reacts to TRADE_CLOSED events (replaces handle_broker_exit_fill)."""

    def test_trade_closed_event_removes_trade(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0, "line_level": None,
            "is_reentry": False, "is_phantom": False,
        })
        assert len(strat.open_trades) == 0

    def test_trade_closed_event_unknown_trade_id(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        # Should not crash
        strat._on_trade_closed({
            "trade_id": "UNKNOWN", "result_type": "SL",
            "exit_price": 90.0, "line_level": None,
            "is_reentry": False, "is_phantom": False,
        })

    def test_trade_closed_event_already_closed_skipped(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "closed",
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0, "line_level": None,
            "is_reentry": False, "is_phantom": False,
        })
        # Should remain
        assert len(strat.open_trades) == 1

    def test_trade_closed_event_creates_reentry(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 100,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "SL",
            "exit_price": 90.0, "line_level": 100,
            "is_reentry": False, "is_phantom": False,
        })
        assert len(strat._reentry_opportunities) == 1

    def test_trade_closed_event_non_sl_does_not_create_reentry(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True))
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "line_level": 100,
        })
        strat._on_trade_closed({
            "trade_id": "T1", "result_type": "TP",
            "exit_price": 130.0, "line_level": 100,
            "is_reentry": False, "is_phantom": False,
        })
        assert len(strat._reentry_opportunities) == 0


# ---------------------------------------------------------------------------
# Misc Methods
# ---------------------------------------------------------------------------

class TestMiscMethods:

    def test_set_entry_filters(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        def f(ctx):
            return (True, "ok")
        strat.set_entry_filters([f])
        assert strat.entry_filters == [f]

    def test_set_triggers(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        def t(s, sid, line, bar):
            return None
        strat.set_triggers([t])
        assert strat.triggers == [t]

    def test_update_strategy_line(self):
        sio = DummySocketIO()
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, event_publisher=sio)
        strat.add_strategy_line("L1", 100.0)
        strat.update_strategy_line("L1", 105.0)
        assert strat.strategy_lines["L1"]["level"] == 105.0
        assert any(e[0] == "line_updated" for e in sio.events)

    def test_update_strategy_line_unknown_id(self):
        # Should not crash
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.update_strategy_line("UNKNOWN", 105.0)

    def test_reset_line_state(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.add_strategy_line("L1", 100.0)
        strat.strategy_lines["L1"]["extreme"] = 50.0
        strat._reset_line_state(strat.strategy_lines["L1"])
        assert strat.strategy_lines["L1"]["extreme"] == 0.0

    def test_reset_trigger_state_is_noop(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        state = {"extreme": 50.0}
        strat._reset_trigger_state(state)
        assert state["extreme"] == 50.0  # base class does nothing

    def test_log_decision_is_noop(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        # Should not crash
        strat.log_decision(1000, "5m", "L1", "TEST", "details")

    def test_ts_to_dt(self):
        dt = BaseLiquidityStrategy._ts_to_dt(0)
        assert dt.tzinfo == timezone.utc
        assert dt.year == 1970

    def test_remove_strategy_line_deletes_trigger_state(self):
        from src.infrastructure.repositories.line_trigger_state_repository import (
            InMemoryLineTriggerStateRepository,
        )
        repo = InMemoryLineTriggerStateRepository()
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS, trigger_state_repo=repo)
        strat.add_strategy_line("L1", 100.0)
        assert "L1" in repo.load_all("MNQ")
        strat.remove_strategy_line("L1")
        assert "L1" not in repo.load_all("MNQ")

    def test_store_and_emit_phantom(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_only=True))
        trade = {
            "pair": "MNQ", "type": "long", "entry": 100,
            "stop_loss": 90, "take_profit": 130, "risk": 10,
            "entry_time": 1000, "status": "open", "is_phantom": True,
        }
        strat._store_and_emit_open(trade)
        assert trade["trade_id"].startswith("phantom-")
        assert len(strat.open_trades) == 1

    def test_store_and_emit_close(self):
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
        # Seed a closed trade
        td = tr.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        trade = {
            "trade_id": td.trade_id, "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "exit_price": 130, "exit_time": 2000,
            "result": 3.0, "status": "closed",
        }
        strat._store_and_emit_close(trade)
        assert len(tr.closed) == 1
        close_events = [e for e in sio.events if e[0] == "trade_close"]
        assert len(close_events) == 1

    def test_check_phantom_exits_warmup_skips(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.execution_context.warmup = True
        strat.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": True,
        })
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        strat._check_phantom_exits(bar)
        assert len(strat.open_trades) == 1

    def test_maybe_remove_line_warmup_on_evaluate_removes(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, line_removal_mode=LineRemovalMode.ON_EVALUATE))
        strat.execution_context.warmup = True
        strat.add_strategy_line("L1", 100.0)
        strat._maybe_remove_line("L1", opened=False)
        assert "L1" not in strat.strategy_lines

    def test_maybe_remove_line_warmup_on_enter_keeps(self):
        strat = _make_base(options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, line_removal_mode=LineRemovalMode.ON_ENTER))
        strat.execution_context.warmup = True
        strat.add_strategy_line("L1", 100.0)
        strat._maybe_remove_line("L1", opened=False)
        assert "L1" in strat.strategy_lines

    def test_on_strategy_bar_warmup_skips_exits(self):
        strat = _make_base(options=DEFAULT_STRATEGY_OPTIONS)
        strat.execution_context.warmup = True
        strat.add_strategy_line("L1", 100.0)
        strat.triggers = [lambda s, sid, line, bar: _make_ctx(s)]
        strat.entry_filters = []
        bar = make_bar(time=1000, close=100, pair="MNQ")
        strat._on_strategy_bar(bar)
        # Warmup should skip storing trade
        assert len(strat.open_trades) == 0
