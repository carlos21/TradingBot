"""Tests for src.strategies.base_strategy."""

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config.models import AccountConfig
from src.domain.events import DomainEvent, EventType
from src.domain.types import Direction
from src.services.trade_manager import TradeManager
from src.strategies.base_strategy import BaseStrategy, DecisionEventCategory
from src.strategies.entry_context import EntryContext, EntryFilter
from tests.conftest import make_bar
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
    MutableTradingContext,
)


def _make_trade_manager(trade_repo=None, event_publisher=None, account_balance=100000.0):
    return TradeManager(
        trade_repository=trade_repo or FakeTradeRepository(),
        socketio=event_publisher or DummySocketIO(),
        trade_executor=FakeTradeExecutor(),
        analytics=FakeAnalyticsReporter(),
        point_value=2.0,
        account_balance=account_balance,
        logger=FakeLogger(),
    )


def _make_base(
    min_stop_loss=10.0,
    event_publisher=None,
    trade_repo=None,
    trade_manager=None,
    extra_sl_space=0.0,
    point_value=2.0,
    account_balance=100000.0,
    risk_per_trade=None,
    risk_pct_per_trade=None,
    fixed_stop_loss=None,
    max_stop_loss=None,
    strategy_tf="5m",
    sl_levels=None,
    rr_ratio=5.0,
    use_fractional_lots=False,
    fee_per_rt=1.5,
    broker_spread=0.0,
    account_configs=None,
    accounts_repo=None,
    live_mode=False,
    execution_context=None,
    logger=None,
):
    return BaseStrategy(
        min_stop_loss=min_stop_loss,
        event_publisher=event_publisher or DummySocketIO(),
        trade_repository=trade_repo or FakeTradeRepository(),
        trade_manager=trade_manager or _make_trade_manager(),
        extra_sl_space=extra_sl_space,
        point_value=point_value,
        account_balance=account_balance,
        risk_per_trade=risk_per_trade,
        risk_pct_per_trade=risk_pct_per_trade,
        fixed_stop_loss=fixed_stop_loss,
        max_stop_loss=max_stop_loss,
        strategy_tf=strategy_tf,
        sl_levels=sl_levels,
        rr_ratio=rr_ratio,
        use_fractional_lots=use_fractional_lots,
        fee_per_rt=fee_per_rt,
        broker_spread=broker_spread,
        logger=logger or FakeLogger(),
        account_configs=account_configs,
        accounts_repo=accounts_repo,
        live_mode=live_mode,
        execution_context=execution_context or MutableTradingContext(warmup=False, trading_enabled=True),
    )


def _make_ctx(strategy, direction=Direction.LONG, close=100.0, extreme=90.0, level=100.0, bar_time=1000):
    bar = make_bar(time=bar_time, close=close, pair="MNQ")
    return EntryContext(
        strategy=strategy,
        line_id="L1",
        direction=direction,
        level=level,
        bar=bar,
        close=close,
        low=bar["low"],
        high=bar["high"],
        extreme=extreme,
        cross_depth=abs(level - extreme),
    )


class TestInit:

    def test_defaults(self):
        s = _make_base()
        assert s.min_stop_loss == 10.0
        assert s.rr_ratio == 5.0
        assert s.strategy_window == 300
        assert s._live_mode is False

    def test_invalid_strategy_tf_raises(self):
        with pytest.raises(ValueError, match="Invalid strategy_tf"):
            _make_base(strategy_tf="")

    def test_hourly_timeframe(self):
        s = _make_base(strategy_tf="1h")
        assert s.strategy_window == 3600

    def test_execution_context_default(self):
        s = _make_base()
        assert s.is_warmup is False
        assert s.execution_context is s._execution_context


class TestWarmupAndExecutionContext:

    def test_is_warmup_true(self):
        ctx = MutableTradingContext(warmup=True)
        s = _make_base(execution_context=ctx)
        assert s.is_warmup is True

    def test_is_warmup_false(self):
        ctx = MutableTradingContext(warmup=False)
        s = _make_base(execution_context=ctx)
        assert s.is_warmup is False


class TestReset:

    def test_reset_clears_state(self):
        s = _make_base()
        s.open_trades.append({"trade_id": "T1"})
        s.total_pnl = 100.0
        s._buf.append(make_bar(time=0))
        s._group_start = 0
        s.reset()
        assert s.open_trades == []
        assert s.total_pnl == 0.0
        assert s._buf == []
        assert s._group_start is None


class TestAccountAndRiskHelpers:

    def test_filter_eligible_accounts_non_live_requires_instrument(self):
        s = _make_base(live_mode=False)
        matched = AccountConfig(name="A1", instrument_symbols=["MNQ"])
        unmatched = AccountConfig(name="A2", instrument_symbols=["ES"])
        no_symbols = AccountConfig(name="A3")
        accounts = [matched, unmatched, no_symbols]
        assert s._filter_eligible_accounts(accounts) == [matched]

    def test_filter_eligible_accounts_live_returns_enabled_and_matched(self):
        s = _make_base(live_mode=True)
        enabled_matched = AccountConfig(name="A1", live_enabled=True, instrument_symbols=["MNQ"])
        enabled_unmatched = AccountConfig(name="A2", live_enabled=True, instrument_symbols=["ES"])
        disabled_matched = AccountConfig(name="A3", live_enabled=False, instrument_symbols=["MNQ"])
        no_symbols = AccountConfig(name="A4", live_enabled=True)
        accounts = [enabled_matched, enabled_unmatched, disabled_matched, no_symbols]
        assert s._filter_eligible_accounts(accounts) == [enabled_matched]

    def test_filter_eligible_accounts_empty(self):
        s = _make_base()
        assert s._filter_eligible_accounts(None) == []
        assert s._filter_eligible_accounts([]) == []

    def test_get_current_account_configs_from_repo(self):
        repo = MagicMock()
        repo.list_accounts.return_value = [
            AccountConfig(name="A1", risk_usd=100, risk_pct=1, instrument_symbols=["MNQ"])
        ]
        s = _make_base(accounts_repo=repo, live_mode=False)
        configs = s._get_current_account_configs()
        assert configs[0].name == "A1"

    def test_get_current_account_configs_repo_exception_falls_back(self):
        repo = MagicMock()
        repo.list_accounts.side_effect = Exception("db fail")
        s = _make_base(
            accounts_repo=repo,
            account_configs=[AccountConfig(name="A2", instrument_symbols=["MNQ"])],
        )
        configs = s._get_current_account_configs()
        assert configs[0].name == "A2"

    def test_get_current_risk_from_accounts(self):
        repo = MagicMock()
        repo.list_accounts.return_value = [
            AccountConfig(name="A1", risk_usd=200, risk_pct=2, instrument_symbols=["MNQ"])
        ]
        s = _make_base(accounts_repo=repo)
        risk_usd, risk_pct = s._get_current_risk()
        assert risk_usd == 200
        assert risk_pct == 2

    def test_get_current_risk_fallback(self):
        s = _make_base(risk_per_trade=150, risk_pct_per_trade=1.5)
        assert s._get_current_risk() == (150, 1.5)


class TestLineApi:

    def test_add_strategy_line_logs_warning(self):
        logger = FakeLogger()
        s = _make_base(logger=logger)
        s.add_strategy_line("L1", 100.0)
        # Should not raise

    def test_remove_strategy_line_noop(self):
        s = _make_base()
        s.remove_strategy_line("L1")

    def test_update_strategy_line_noop(self):
        s = _make_base()
        s.update_strategy_line("L1", 100.0)


class TestLogDecision:

    def test_log_decision_is_noop(self):
        s = _make_base()
        s.log_decision(
            bar_time=1000,
            tf="5m",
            line_id="L1",
            event="TEST",
            details="details",
            category=DecisionEventCategory.TRADE_ACTION,
        )


class TestBarAggregation:

    def test_on_raw_bar_aggregates_and_calls_subclass(self):
        s = _make_base(strategy_tf="5m")
        processed = []
        s._on_strategy_bar = lambda bar: processed.append(bar)

        s.on_raw_bar(make_bar(time=0, close=100, pair="MNQ"))
        assert len(processed) == 0
        s.on_raw_bar(make_bar(time=300, close=101, pair="MNQ"))
        assert len(processed) == 1
        assert processed[0]["close"] == 100

    def test_on_raw_bar_not_implemented_default(self):
        s = _make_base(strategy_tf="5m")
        s.on_raw_bar(make_bar(time=0, close=100, pair="MNQ"))
        with pytest.raises(NotImplementedError):
            s.on_raw_bar(make_bar(time=300, close=101, pair="MNQ"))


class TestFiltersAllowEntry:

    def test_all_pass(self):
        s = _make_base()
        s.entry_filters = [lambda _ctx: (True, "ok")]
        ctx = MagicMock()
        allow, reason, hold = s._filters_allow_entry(ctx)
        assert allow is True
        assert hold is False

    def test_one_blocks(self):
        blocker = EntryFilter(fn=lambda _ctx: (False, "blocked"), name="blocker")
        s = _make_base()
        s.entry_filters = [blocker]
        ctx = MagicMock()
        allow, reason, hold = s._filters_allow_entry(ctx)
        assert allow is False
        assert "blocker" in reason

    def test_hold_on_block(self):
        holder = EntryFilter(fn=lambda _ctx: (False, "hold"), name="holder", hold_on_block=True)
        s = _make_base()
        s.entry_filters = [holder]
        ctx = MagicMock()
        allow, reason, hold = s._filters_allow_entry(ctx)
        assert allow is False
        assert hold is True


class TestBreakeven:

    def test_long_breakeven_triggered(self):
        sio = DummySocketIO()
        tm = _make_trade_manager(event_publisher=sio)
        s = _make_base(
            event_publisher=sio,
            trade_manager=tm,
            trade_repo=tm.trade_repository,
        )
        s.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
            "breakeven_config": {"trigger_rr": 2.0, "move_to_rr": 0.05},
        })
        tm.open_trades.append(s.open_trades[0])
        tm.trade_repository.inserted.append({"trade_id": "T1", "stop_loss": 90})

        s._check_breakeven(make_bar(time=1000, high=121, low=100, pair="MNQ"))
        assert s.open_trades[0]["stop_loss"] == pytest.approx(100.5, abs=0.01)

    def test_short_breakeven_triggered(self):
        sio = DummySocketIO()
        tm = _make_trade_manager(event_publisher=sio)
        s = _make_base(
            event_publisher=sio,
            trade_manager=tm,
            trade_repo=tm.trade_repository,
        )
        s.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "short",
            "entry": 100, "stop_loss": 110, "take_profit": 70,
            "risk": 10, "status": "open",
            "breakeven_config": {"trigger_rr": 2.0, "move_to_rr": 0.05},
        })
        tm.open_trades.append(s.open_trades[0])
        tm.trade_repository.inserted.append({"trade_id": "T1", "stop_loss": 110})

        s._check_breakeven(make_bar(time=1000, high=100, low=79, pair="MNQ"))
        assert s.open_trades[0]["stop_loss"] == pytest.approx(99.5, abs=0.01)

    def test_breakeven_warmup_skips(self):
        s = _make_base(execution_context=MutableTradingContext(warmup=True))
        s.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
            "breakeven_config": {"trigger_rr": 2.0, "move_to_rr": 0.05},
        })
        s._check_breakeven(make_bar(time=1000, high=121, low=100, pair="MNQ"))
        assert s.open_trades[0]["stop_loss"] == 90

    def test_breakeven_missing_config_skips(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
        })
        s._check_breakeven(make_bar(time=1000, high=121, low=100, pair="MNQ"))
        assert s.open_trades[0]["stop_loss"] == 90

    def test_breakeven_not_triggered(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
            "breakeven_config": {"trigger_rr": 2.0, "move_to_rr": 0.05},
        })
        s._check_breakeven(make_bar(time=1000, high=115, low=100, pair="MNQ"))
        assert s.open_trades[0]["stop_loss"] == 90

    def test_breakeven_dict_config_supported(self):
        sio = DummySocketIO()
        tm = _make_trade_manager(event_publisher=sio)
        s = _make_base(
            event_publisher=sio,
            trade_manager=tm,
            trade_repo=tm.trade_repository,
        )
        s.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open",
            "breakeven_config": {"trigger_rr": 2.0, "move_to_rr": 0.05},
        })
        tm.open_trades.append(s.open_trades[0])
        tm.trade_repository.inserted.append({"trade_id": "T1", "stop_loss": 90})
        s._check_breakeven(make_bar(time=1000, high=121, low=100, pair="MNQ"))
        assert s.open_trades[0]["stop_loss"] == pytest.approx(100.5, abs=0.01)


class TestTradeUpdated:

    def test_on_trade_updated_syncs_fields(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "T1", "status": "open",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "contracts": 1,
        })
        s._on_trade_updated({
            "trade_id": "T1",
            "entry_price": 99.5,
            "stop_loss": 89.5,
            "take_profit": 131.0,
            "risk": 10.5,
            "contracts": 2,
        })
        t = s.open_trades[0]
        assert t["entry"] == 99.5
        assert t["stop_loss"] == 89.5
        assert t["take_profit"] == 131.0
        assert t["risk"] == 10.5
        assert t["contracts"] == 2

    def test_on_trade_updated_unknown_id_is_noop(self):
        s = _make_base()
        s.open_trades.append({"trade_id": "T1", "status": "open"})
        s._on_trade_updated({"trade_id": "UNKNOWN"})

    def test_on_trade_updated_closed_trade_is_noop(self):
        s = _make_base()
        s.open_trades.append({"trade_id": "T1", "status": "closed"})
        s._on_trade_updated({"trade_id": "T1", "entry_price": 99.5})
        assert "entry" not in s.open_trades[0]


class TestUpdateTradeSl:

    def test_update_trade_sl_updates_sibling_trades(self):
        sio = DummySocketIO()
        tm = _make_trade_manager(event_publisher=sio)
        s = _make_base(event_publisher=sio, trade_manager=tm, trade_repo=tm.trade_repository)
        trade = {
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "signal_id": "SIG1",
        }
        s.open_trades.append(trade)
        tm.open_trades.append(trade)
        tm.trade_repository.inserted.append({"trade_id": "T1", "stop_loss": 90})

        s._update_trade_sl(trade, 95.0)
        assert trade["stop_loss"] == 95.0
        assert ("T1", 95.0) in tm.trade_executor.sl_updates


class TestOnEvent:

    def test_trade_closed_event(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "status": "open",
        })
        s.on_event(DomainEvent(
            EventType.TRADE_CLOSED,
            payload={"trade_id": "T1", "exit_price": 90.0, "result_type": "SL"},
        ))
        assert len(s.open_trades) == 0

    def test_trade_updated_event(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "T1", "status": "open",
            "entry": 100, "stop_loss": 90,
        })
        s.on_event(DomainEvent(
            EventType.TRADE_UPDATED,
            payload={"trade_id": "T1", "stop_loss": 89.0},
        ))
        assert s.open_trades[0]["stop_loss"] == 89.0


class TestOnTradeClosed:

    def test_sl_closes_trade(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "status": "open",
        })
        s._on_trade_closed({"trade_id": "T1", "exit_price": 90.0, "result_type": "SL"})
        assert len(s.open_trades) == 0

    def test_unknown_trade_is_noop(self):
        s = _make_base()
        s._on_trade_closed({"trade_id": "UNKNOWN"})

    def test_already_closed_trade_is_noop(self):
        s = _make_base()
        s.open_trades.append({"trade_id": "T1", "status": "closed"})
        s._on_trade_closed({"trade_id": "T1"})
        assert len(s.open_trades) == 1


class TestPhantomExits:

    def test_phantom_long_sl_hit(self):
        sio = DummySocketIO()
        s = _make_base(event_publisher=sio)
        s.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": True,
        })
        s._check_phantom_exits(make_bar(time=1000, low=85, high=95, pair="MNQ"))
        assert len(s.open_trades) == 0
        assert any(e[0] == "trade_close" for e in sio.events)

    def test_phantom_long_tp_hit(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": True,
        })
        s._check_phantom_exits(make_bar(time=1000, low=100, high=135, pair="MNQ"))
        assert len(s.open_trades) == 0

    def test_phantom_short_sl_hit(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "short",
            "entry": 100, "stop_loss": 110, "take_profit": 70,
            "risk": 10, "status": "open", "is_phantom": True,
        })
        s._check_phantom_exits(make_bar(time=1000, low=100, high=115, pair="MNQ"))
        assert len(s.open_trades) == 0

    def test_phantom_short_tp_hit(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "short",
            "entry": 100, "stop_loss": 110, "take_profit": 70,
            "risk": 10, "status": "open", "is_phantom": True,
        })
        s._check_phantom_exits(make_bar(time=1000, low=65, high=100, pair="MNQ"))
        assert len(s.open_trades) == 0

    def test_phantom_no_hit_keeps_trade(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": True,
        })
        s._check_phantom_exits(make_bar(time=1000, low=95, high=110, pair="MNQ"))
        assert len(s.open_trades) == 1

    def test_phantom_warmup_skips(self):
        s = _make_base(execution_context=MutableTradingContext(warmup=True))
        s.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": True,
        })
        s._check_phantom_exits(make_bar(time=1000, low=85, high=95, pair="MNQ"))
        assert len(s.open_trades) == 1

    def test_real_trade_ignored(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": False,
        })
        s._check_phantom_exits(make_bar(time=1000, low=85, high=95, pair="MNQ"))
        assert len(s.open_trades) == 1

    def test_phantom_same_bar_skips(self):
        s = _make_base()
        s.open_trades.append({
            "trade_id": "phantom-1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "status": "open", "is_phantom": True,
            "entry_time": 1000,
        })
        s._check_phantom_exits(make_bar(time=1000, low=85, high=95, pair="MNQ"))
        assert len(s.open_trades) == 1


class TestSLSelection:

    def test_select_sl_level_picks_smallest_covering(self):
        s = _make_base(sl_levels=[10, 15, 20, 30, 40])
        assert s._select_sl_level(17.0) == 20.0

    def test_select_sl_level_exceeds_all_picks_largest(self):
        s = _make_base(sl_levels=[10, 15, 20, 30, 40])
        assert s._select_sl_level(100.0) == 40.0


class TestBuildTradeFromContext:

    def test_long_fixed_sl(self):
        s = _make_base(fixed_stop_loss=20)
        ctx = _make_ctx(s, direction=Direction.LONG, close=100, extreme=85)
        trade = s._build_trade_from_context(ctx)
        assert trade["type"] == "long"
        assert trade["stop_loss"] == 80.0
        assert trade["take_profit"] == pytest.approx(100 + 5 * 20, abs=0.1)
        assert trade["risk"] == 20.0

    def test_short_fixed_sl(self):
        s = _make_base(fixed_stop_loss=20)
        ctx = _make_ctx(s, direction=Direction.SHORT, close=100, extreme=115)
        trade = s._build_trade_from_context(ctx)
        assert trade["type"] == "short"
        assert trade["stop_loss"] == 120.0
        assert trade["risk"] == 20.0

    def test_tiered_sl(self):
        s = _make_base(sl_levels=[10, 15, 20, 30, 40])
        ctx = _make_ctx(s, direction=Direction.LONG, close=100, extreme=83)
        trade = s._build_trade_from_context(ctx)
        assert trade["risk"] == 20.0

    def test_dynamic_sl_with_extra_space(self):
        s = _make_base(extra_sl_space=2.0)
        ctx = _make_ctx(s, direction=Direction.LONG, close=100, extreme=83)
        trade = s._build_trade_from_context(ctx)
        assert trade["risk"] == pytest.approx(19.0, abs=0.1)  # max(17,10)+2

    def test_max_stop_loss_cap(self):
        s = _make_base(max_stop_loss=25)
        ctx = _make_ctx(s, direction=Direction.LONG, close=100, extreme=70)
        trade = s._build_trade_from_context(ctx)
        assert trade["risk"] == 25.0


class TestCalcContracts:

    def test_calc_contracts_normal(self):
        s = _make_base(risk_pct_per_trade=1.0)
        # risk_budget = 100000 * 1% = 1000; risk_per_contract = 20 * 2 = 40; contracts = 25
        assert s._calc_contracts(40.0) == 25.0

    def test_calc_contracts_zero_risk_per_contract(self):
        s = _make_base()
        assert s._calc_contracts(0.0) == 1.0
        s_frac = _make_base(use_fractional_lots=True)
        assert s_frac._calc_contracts(0.0) == 0.01

    def test_calc_contracts_uses_trade_manager_balance(self):
        tm = _make_trade_manager(account_balance=50000.0)
        s = _make_base(trade_manager=tm, risk_pct_per_trade=1.0)
        # risk_budget = 500; 500/40 = 12.5 -> round-half-up = 13
        assert s._calc_contracts(40.0) == 13

    def test_calc_contracts_fractional_lots(self):
        s = _make_base(use_fractional_lots=True, risk_pct_per_trade=1.0)
        assert s._calc_contracts(40.0) == 25.0


class TestMakeTradeDict:

    def test_make_trade_dict_long(self):
        s = _make_base()
        bar = make_bar(time=1000, pair="MNQ")
        trade = s._make_trade_dict(bar, "long", 100, 90, 130, 10)
        assert trade["pair"] == "MNQ"
        assert trade["type"] == "long"
        assert trade["entry"] == 100
        assert trade["stop_loss"] == 90
        assert trade["take_profit"] == 130
        assert trade["risk"] == 10
        assert trade["status"] == "open"
        assert trade["entry_time"] == 1000

    def test_make_trade_dict_risk_pct_none_when_zero_balance(self):
        tm = _make_trade_manager(account_balance=0.0)
        s = _make_base(trade_manager=tm, account_balance=0.0)
        bar = make_bar(time=1000, pair="MNQ")
        trade = s._make_trade_dict(bar, "long", 100, 90, 130, 10)
        assert trade["risk_pct"] is None


class TestStoreAndEmit:

    def test_store_and_emit_open_persists_trade(self):
        sio = DummySocketIO()
        tm = _make_trade_manager(event_publisher=sio)
        s = _make_base(event_publisher=sio, trade_manager=tm, trade_repo=tm.trade_repository)
        trade = {
            "pair": "MNQ", "type": "long", "entry": 100,
            "stop_loss": 90, "take_profit": 130, "risk": 10,
            "entry_time": 1000, "status": "open",
        }
        s._store_and_emit_open(trade)
        assert trade["trade_id"] is not None
        assert len(s.open_trades) == 1
        assert len(tm.trade_repository.inserted) == 1
        assert any(e[0] == "trade_open" for e in sio.events)

    def test_tm_registration_includes_instrument(self):
        """Regression: modify/close commands resolve the instrument from
        tm.open_trades — the registered copy must carry it."""
        sio = DummySocketIO()
        tm = TradeManager(
            trade_repository=FakeTradeRepository(),
            socketio=sio,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
            instrument="MNQ 09-26",
        )
        s = _make_base(event_publisher=sio, trade_manager=tm, trade_repo=tm.trade_repository)
        trade = {
            "pair": "MNQ", "type": "long", "entry": 100,
            "stop_loss": 90, "take_profit": 130, "risk": 10,
            "entry_time": 1000, "status": "open",
        }
        s._store_and_emit_open(trade)
        assert len(tm.open_trades) == 1
        assert tm.open_trades[0]["instrument"] == "MNQ 09-26"

    def test_store_and_emit_open_multi_account(self):
        sio = DummySocketIO()
        tm = _make_trade_manager(event_publisher=sio)
        accounts = [
            AccountConfig(name="A1", risk_usd=None, risk_pct=None, instrument_symbols=["MNQ"]),
            AccountConfig(name="A2", risk_usd=None, risk_pct=None, instrument_symbols=["MNQ"]),
        ]
        s = _make_base(
            event_publisher=sio,
            trade_manager=tm,
            trade_repo=tm.trade_repository,
            account_configs=accounts,
        )
        trade = {
            "pair": "MNQ", "type": "long", "entry": 100,
            "stop_loss": 90, "take_profit": 130, "risk": 10,
            "entry_time": 1000, "status": "open",
        }
        s._store_and_emit_open(trade)
        assert len(s.open_trades) == 2
        accounts_opened = {t.get("account") for t in s.open_trades}
        assert accounts_opened == {"A1", "A2"}

    def test_store_and_emit_open_warmup_returns_empty(self):
        sio = DummySocketIO()
        tm = _make_trade_manager(event_publisher=sio)
        s = _make_base(
            event_publisher=sio,
            trade_manager=tm,
            trade_repo=tm.trade_repository,
            execution_context=MutableTradingContext(warmup=True),
        )
        trade = {
            "pair": "MNQ", "type": "long", "entry": 100,
            "stop_loss": 90, "take_profit": 130, "risk": 10,
            "entry_time": 1000, "status": "open",
        }
        s._store_and_emit_open(trade)
        assert len(s.open_trades) == 0

    def test_store_and_emit_close(self):
        sio = DummySocketIO()
        tm = _make_trade_manager(event_publisher=sio)
        s = _make_base(event_publisher=sio, trade_manager=tm, trade_repo=tm.trade_repository)
        td = tm.trade_repository.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        trade = {
            "trade_id": td.trade_id, "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "exit_price": 130, "exit_time": 2000,
            "status": "closed",
        }
        s._store_and_emit_close(trade)
        assert len(tm.trade_repository.closed) == 1
        assert any(e[0] == "trade_close" for e in sio.events)


class TestTsToDt:

    def test_ts_to_dt_utc(self):
        dt = BaseStrategy._ts_to_dt(0)
        assert dt.tzinfo == timezone.utc
        assert dt.year == 1970

    def test_ts_to_dt_now(self):
        dt = BaseStrategy._ts_to_dt(1700000000)
        assert dt.year == 2023
