"""E2E tests for the independent-trade multi-account model.

Every trade is now an independent DB row with its own account. The
StrategyTradeService expands a signal into one trade per configured account;
MultiAccountExecutor is a thin DB-aware passthrough.

Run with: python -m pytest tests/test_multi_account_e2e.py -v
"""

from __future__ import annotations

import dataclasses
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.application.services.strategy_trade_service import StrategyTradeService
from src.domain.events import EventType
from src.events.event_bus import EventBus
from src.infrastructure.event_publisher import DomainEventBusPublisher
from src.infrastructure.gateway.executor import MultiAccountExecutor, ZMQTradeExecutor
from src.services.trade_manager import TradeManager
from src.strategies.base_strategy import BreakevenConfig
from src.strategies.liquidity_v2.base_strategy import BaseLiquidityStrategy
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLineRepository,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

class AccountConfig:
    """Minimal stand-in for src.config.models.AccountConfig."""

    def __init__(
        self,
        name: str,
        risk_usd: float | None = None,
        risk_pct: float | None = None,
        rr_ratio: float | None = None,
    ):
        self.name = name
        self.risk_usd = risk_usd
        self.risk_pct = risk_pct
        self.rr_ratio = rr_ratio


def _make_trade_manager() -> TradeManager:
    return TradeManager(
        trade_repository=FakeTradeRepository(),
        socketio=DummySocketIO(),
        trade_executor=FakeTradeExecutor(),
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
    )


def _signal_trade(signal_id: str = "S1") -> dict:
    return {
        "trade_id": signal_id,
        "pair": "MNQ",
        "type": "long",
        "entry": 100.0,
        "stop_loss": 90.0,
        "take_profit": 130.0,
        "risk": 10.0,
        "entry_time": 1000.0,
        "rr_ratio": 5.0,
    }


class FakeGateway:
    """Records all ZMQ commands for verification."""

    def __init__(self):
        self.open_orders: list[dict] = []
        self.close_orders: list[dict] = []
        self.modify_orders: list[dict] = []

    def send_open_order(self, **kwargs):
        self.open_orders.append(kwargs)

    def send_close_order(self, **kwargs):
        self.close_orders.append(kwargs)

    def send_modify_order(self, **kwargs):
        self.modify_orders.append(kwargs)


class FakeZMQExecutor(ZMQTradeExecutor):
    """ZMQTradeExecutor with a FakeGateway instead of real TradingGateway."""

    def __init__(self, gateway: FakeGateway, logger: FakeLogger):
        # bypass ZMQTradeExecutor.__init__ which requires TradingGateway type
        self._gateway = gateway
        self.logger = logger
        self._risk_usd = None
        self._risk_pct = None


def _make_strategy(
    event_bus: EventBus,
    trade_manager: TradeManager,
    trade_repo: FakeTradeRepository,
    options=None,
    account_configs=None,
) -> BaseLiquidityStrategy:
    publisher = DomainEventBusPublisher(event_bus)
    strategy = BaseLiquidityStrategy(
        min_stop_loss=10.0,
        max_bounce=90.0,
        event_publisher=publisher,
        line_repository=FakeLineRepository(),
        trade_repository=trade_repo,
        trade_manager=trade_manager,
        extra_sl_space=0.0,
        fixed_stop_loss=20,
        options=options or dataclasses.replace(DEFAULT_STRATEGY_OPTIONS, reentry_after_sl=True),
        sl_levels=None,
        rr_ratio=3.3,
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
        account_configs=account_configs,
    )
    event_bus.add_subscriber(EventType.TRADE_CLOSED, strategy)
    return strategy


# ═══════════════════════════════════════════════════════════════════════════════
# StrategyTradeService expansion
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategyTradeServiceExpansion:
    """Test signal → independent per-account trade creation."""

    def _service(self, tm: TradeManager) -> StrategyTradeService:
        return StrategyTradeService(
            trade_repository=tm.trade_repository,
            trade_executor=tm.trade_executor,
            event_publisher=tm.socketio,
            logger=FakeLogger(),
            point_value=2.0,
            account_balance=100000.0,
        )

    def test_expands_to_all_accounts(self):
        tm = _make_trade_manager()
        service = self._service(tm)

        opened = service.open_trade(
            trade=_signal_trade("S1"),
            is_warmup=False,
            account_configs=[
                AccountConfig("A1", rr_ratio=3.0),
                AccountConfig("A2", rr_ratio=5.0),
                AccountConfig("A3", rr_ratio=2.0),
            ],
        )

        assert len(opened) == 3
        accounts = {t["account"] for t in opened}
        assert accounts == {"A1", "A2", "A3"}
        # All siblings share a signal_id
        signal_ids = {t.get("signal_id") for t in opened}
        assert len(signal_ids) == 1
        assert all(t.get("signal_id") for t in opened)

    def test_per_account_take_profit_recalculation(self):
        tm = _make_trade_manager()
        service = self._service(tm)

        opened = service.open_trade(
            trade=_signal_trade("S1"),
            is_warmup=False,
            account_configs=[
                AccountConfig("A1", rr_ratio=2.0),
                AccountConfig("A2", rr_ratio=4.0),
            ],
        )

        tps = [t["take_profit"] for t in opened]
        assert 119.0 < tps[0] < 121.0  # ~120 for rr=2
        assert 139.0 < tps[1] < 141.0  # ~140 for rr=4

    def test_per_account_risk_override(self):
        tm = _make_trade_manager()
        service = self._service(tm)

        opened = service.open_trade(
            trade=_signal_trade("S1"),
            is_warmup=False,
            account_configs=[
                AccountConfig("A1", risk_usd=500.0),
                AccountConfig("A2", risk_usd=1000.0),
            ],
        )

        # Risk override is consumed by the use case; the opened trade dicts
        # carry the resulting contracts (positive) and risk dollars.
        risk_dollars = {t["risk_dollars"] for t in opened}
        assert 500.0 in risk_dollars
        assert 1000.0 in risk_dollars

    def test_single_trade_when_no_account_configs(self):
        """With no accounts configured the service opens one strategy trade."""
        tm = _make_trade_manager()
        service = self._service(tm)

        opened = service.open_trade(
            trade=_signal_trade("S1"),
            is_warmup=False,
            account_configs=[],
        )

        assert len(opened) == 1
        assert opened[0].get("account") is None
        assert opened[0].get("signal_id") is None

    def test_warmup_returns_empty(self):
        tm = _make_trade_manager()
        service = self._service(tm)

        opened = service.open_trade(
            trade=_signal_trade("S1"),
            is_warmup=True,
            account_configs=[AccountConfig("A1")],
        )

        assert opened == []

    def test_source_propagates_to_account_trades(self):
        tm = _make_trade_manager()
        service = self._service(tm)

        signal = {**_signal_trade("S1"), "source": "test"}
        opened = service.open_trade(
            trade=signal,
            is_warmup=False,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
        )

        for t in opened:
            db = tm.trade_repository.get_trade(t["trade_id"])
            assert db.source == "test"

    def test_source_strategy_is_default_when_not_set(self):
        tm = _make_trade_manager()
        service = self._service(tm)

        opened = service.open_trade(
            trade=_signal_trade("S1"),
            is_warmup=False,
            account_configs=[AccountConfig("A1")],
        )

        db = tm.trade_repository.get_trade(opened[0]["trade_id"])
        assert db.source == "strategy"


# ═══════════════════════════════════════════════════════════════════════════════
# MultiAccountExecutor passthrough
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiAccountExecutor:
    """MultiAccountExecutor is now a DB-aware passthrough."""

    def test_on_trade_open_forwards_with_account(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "account": "A1",
        }
        executor.on_trade_open(trade)

        assert len(gateway.open_orders) == 1
        assert gateway.open_orders[0]["trade_id"] == "T1"
        assert gateway.open_orders[0].get("account") == "A1"

    def test_on_trade_open_rejects_missing_account(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        trade = {"trade_id": "T1", "pair": "MNQ", "type": "long", "entry": 100.0}
        with pytest.raises(RuntimeError, match="no account"):
            executor.on_trade_open(trade)

    def test_on_trade_close_looks_up_account_from_db(self):
        tm = _make_trade_manager()
        tm.trade_repository.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="A1",
        )

        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_close("T1", 110.0)

        assert len(gateway.close_orders) == 1
        assert gateway.close_orders[0]["trade_id"] == "T1"
        assert gateway.close_orders[0].get("account") == "A1"

    def test_on_trade_close_dedup_within_window(self):
        tm = _make_trade_manager()
        tm.trade_repository.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="A1",
        )

        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_close("T1", 110.0)
        executor.on_trade_close("T1", 110.0)

        assert len(gateway.close_orders) == 1

    def test_on_sl_update_looks_up_account_from_db(self):
        tm = _make_trade_manager()
        tm.trade_repository.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="A1",
        )

        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_sl_update("T1", 95.0)

        assert len(gateway.modify_orders) == 1
        assert gateway.modify_orders[0] == {
            "trade_id": "T1",
            "stop_loss": 95.0,
            "account": "A1",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# TradeManager + stale account cleanup
# ═══════════════════════════════════════════════════════════════════════════════

class TestTradeManagerResumeAndStaleCleanup:
    """DB resume now loads every open trade and cleans stale accounts."""

    def test_loads_all_open_trades(self):
        repo = FakeTradeRepository()
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="A1",
            source="strategy",
        )
        repo.insert_trade(
            pair="MNQ",
            trade_type="short",
            entry_price=200.0,
            stop_loss=210.0,
            take_profit=170.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T2",
            account="A2",
            source="strategy",
        )

        tm = TradeManager(
            trade_repository=repo,
            socketio=DummySocketIO(),
            trade_executor=FakeTradeExecutor(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )

        assert len(tm.open_trades) == 2
        assert {t["trade_id"] for t in tm.open_trades} == {"T1", "T2"}


# ═══════════════════════════════════════════════════════════════════════════════
# Broker fill handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrokerFills:
    """Broker fills operate on independent trades."""

    def _setup(self):
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        repo = FakeTradeRepository()
        tm = TradeManager(
            trade_repository=repo,
            socketio=publisher,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        return event_bus, repo, tm

    def test_entry_fill_updates_correct_trade(self):
        _, repo, tm = self._setup()
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="A1",
        )
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T2",
            account="A2",
        )
        tm.open_trades = [
            {"trade_id": "T1", "pair": "MNQ", "type": "long", "entry": 100.0, "stop_loss": 90.0, "take_profit": 130.0, "risk": 10.0, "contracts": 1, "entry_time": 1000.0, "account": "A1", "status": "open"},
            {"trade_id": "T2", "pair": "MNQ", "type": "long", "entry": 100.0, "stop_loss": 90.0, "take_profit": 130.0, "risk": 10.0, "contracts": 1, "entry_time": 1000.0, "account": "A2", "status": "open"},
        ]

        tm.handle_broker_entry_fill("T1", entry_price=99.5, stop_loss=89.5, take_profit=129.5)

        t1 = repo.get_trade("T1")
        t2 = repo.get_trade("T2")
        assert t1.entry_price == 99.5
        assert t1.stop_loss == 89.5
        assert t2.entry_price == 100.0

    def test_broker_fill_closes_correct_trade_and_updates_balance(self):
        _, repo, tm = self._setup()
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="A1",
        )
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T2",
            account="A2",
        )
        tm.open_trades = [
            {"trade_id": "T1", "pair": "MNQ", "type": "long", "entry": 100.0, "stop_loss": 90.0, "take_profit": 130.0, "risk": 10.0, "contracts": 1, "entry_time": 1000.0, "account": "A1", "status": "open"},
            {"trade_id": "T2", "pair": "MNQ", "type": "long", "entry": 100.0, "stop_loss": 90.0, "take_profit": 130.0, "risk": 10.0, "contracts": 1, "entry_time": 1000.0, "account": "A2", "status": "open"},
        ]

        initial_balance = tm.account_balance
        tm.handle_broker_fill("T1", exit_price=130.0, result_type="TP")

        assert repo.get_trade("T1").exit_time is not None
        assert repo.get_trade("T2").exit_time is None
        assert tm.account_balance > initial_balance

    def test_duplicate_exit_fill_is_idempotent(self):
        _, repo, tm = self._setup()
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="A1",
        )
        tm.open_trades = [
            {"trade_id": "T1", "pair": "MNQ", "type": "long", "entry": 100.0, "stop_loss": 90.0, "take_profit": 130.0, "risk": 10.0, "contracts": 1, "entry_time": 1000.0, "account": "A1", "status": "open"},
        ]

        tm.handle_broker_fill("T1", exit_price=90.0, result_type="SL")
        balance_after_first = tm.account_balance

        tm.handle_broker_fill("T1", exit_price=90.0, result_type="SL")
        balance_after_second = tm.account_balance

        assert balance_after_first == balance_after_second
        assert len(repo.closed) == 1

    def test_unknown_trade_id_fill_is_graceful(self):
        _, repo, tm = self._setup()

        tm.handle_broker_fill("UNKNOWN-TRADE-123", exit_price=90.0, result_type="SL")

        assert len(repo.closed) == 0
        assert tm.account_balance == 100000.0


# ═══════════════════════════════════════════════════════════════════════════════
# ZMQTradeExecutor account routing
# ═══════════════════════════════════════════════════════════════════════════════

class TestZMQTradeExecutorAccountRouting:
    """Verify ZMQTradeExecutor passes account through to gateway for opens."""

    def test_open_order_includes_account(self):
        gateway = MagicMock()
        executor = ZMQTradeExecutor(gateway, FakeLogger())

        trade = {
            "trade_id": "T1",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "account": "Sim101",
        }
        executor.on_trade_open(trade)

        gateway.send_open_order.assert_called_once()
        assert gateway.send_open_order.call_args[1]["account"] == "Sim101"

    def test_close_order_does_not_include_account(self):
        """ZMQTradeExecutor.on_trade_close does not pass account.

        MultiAccountExecutor bypasses it and calls gateway directly.
        """
        gateway = MagicMock()
        executor = ZMQTradeExecutor(gateway, FakeLogger())

        executor.on_trade_close("T1", 110.0)
        gateway.send_close_order.assert_called_once_with(
            trade_id="T1", reason="strategy"
        )

    def test_modify_order_does_not_include_account(self):
        gateway = MagicMock()
        executor = ZMQTradeExecutor(gateway, FakeLogger())

        executor.on_sl_update("T1", 95.0)
        gateway.send_modify_order.assert_called_once_with(
            trade_id="T1", stop_loss=95.0
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Reentry integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestReentryIntegration:
    """SL fills create re-entry opportunities via the event bus."""

    def _setup(self):
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        repo = FakeTradeRepository()
        tm = TradeManager(
            trade_repository=repo,
            socketio=publisher,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        return event_bus, repo, tm

    def test_broker_sl_fill_creates_reentry(self):
        event_bus, repo, tm = self._setup()
        strategy = _make_strategy(event_bus, tm, repo)

        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "status": "open",
            "line_level": 95.0,
            "is_reentry": False,
            "entry_time": 1000.0,
            "account": "Sim101",
        }
        strategy.open_trades.append(trade)
        tm.open_trades.append(dict(trade))
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="Sim101",
        )

        tm.handle_broker_fill("T1", exit_price=90.0, result_type="SL")

        assert len(strategy.open_trades) == 0
        assert len(strategy._reentry_opportunities) == 1
        opp = strategy._reentry_opportunities[0]
        assert opp["level"] == 95.0
        assert opp["direction"] == "long"
        assert opp["pair"] == "MNQ"

    def test_broker_tp_fill_does_not_create_reentry(self):
        event_bus, repo, tm = self._setup()
        strategy = _make_strategy(event_bus, tm, repo)

        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "status": "open",
            "line_level": 95.0,
            "is_reentry": False,
            "entry_time": 1000.0,
            "account": "Sim101",
        }
        strategy.open_trades.append(trade)
        tm.open_trades.append(dict(trade))
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="Sim101",
        )

        tm.handle_broker_fill("T1", exit_price=130.0, result_type="TP")

        assert len(strategy.open_trades) == 0
        assert len(strategy._reentry_opportunities) == 0

    def test_reentry_trade_does_not_chain(self):
        event_bus, repo, tm = self._setup()
        strategy = _make_strategy(event_bus, tm, repo)

        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "status": "open",
            "line_level": 95.0,
            "is_reentry": True,
            "entry_time": 1000.0,
            "account": "Sim101",
        }
        strategy.open_trades.append(trade)
        tm.open_trades.append(dict(trade))
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="Sim101",
        )

        tm.handle_broker_fill("T1", exit_price=90.0, result_type="SL")

        assert len(strategy.open_trades) == 0
        assert len(strategy._reentry_opportunities) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Live end-to-end flows
# ═══════════════════════════════════════════════════════════════════════════════

class TestLiveFlows:
    """Live-mode scenarios with executor + TradeManager + strategy."""

    def test_breakeven_sends_modify_for_single_trade(self):
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        repo = FakeTradeRepository()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=None,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        tm = TradeManager(
            trade_repository=repo,
            socketio=publisher,
            trade_executor=executor,
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        executor.trade_manager = tm
        strategy = _make_strategy(
            event_bus,
            tm,
            repo,
            options=dataclasses.replace(
                DEFAULT_STRATEGY_OPTIONS,
                reentry_after_sl=True,
                breakeven=BreakevenConfig(trigger_rr=1.0, move_to_rr=0.0),
            ),
        )

        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="Sim101",
        )
        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "status": "open",
            "line_level": 95.0,
            "is_reentry": False,
            "entry_time": 1000.0,
            "account": "Sim101",
        }
        strategy.open_trades.append(trade)
        tm.open_trades.append(dict(trade))

        bar = {
            "time": 2000.0,
            "pair": "MNQ",
            "open": 100.0,
            "high": 110.0,
            "low": 100.0,
            "close": 105.0,
        }
        strategy.check_breakeven(bar)

        assert len(gateway.modify_orders) == 1
        assert gateway.modify_orders[0]["trade_id"] == "T1"
        assert gateway.modify_orders[0]["stop_loss"] == 100.0
        assert gateway.modify_orders[0]["account"] == "Sim101"

    def test_session_end_close_sends_close_commands(self):
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        repo = FakeTradeRepository()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=None,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        tm = TradeManager(
            trade_repository=repo,
            socketio=publisher,
            trade_executor=executor,
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        executor.trade_manager = tm
        strategy = _make_strategy(event_bus, tm, repo)

        for tid, acct in (("T1", "A1"), ("T2", "A2")):
            repo.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=130.0,
                risk=10.0,
                entry_time=datetime.now(tz=timezone.utc),
                trade_id=tid,
                account=acct,
            )
            tm.open_trades.append(
                {
                    "trade_id": tid,
                    "pair": "MNQ",
                    "type": "long",
                    "entry": 100.0,
                    "stop_loss": 90.0,
                    "take_profit": 130.0,
                    "risk": 10.0,
                    "contracts": 1,
                    "entry_time": 1000.0,
                    "account": acct,
                    "status": "open",
                }
            )
            strategy.open_trades.append(
                {
                    "trade_id": tid,
                    "pair": "MNQ",
                    "type": "long",
                    "entry": 100.0,
                    "stop_loss": 90.0,
                    "take_profit": 130.0,
                    "risk": 10.0,
                    "status": "open",
                    "line_level": 95.0,
                    "is_reentry": False,
                    "entry_time": 1000.0,
                    "account": acct,
                }
            )

        tm.close_trade("T1", exit_price=105.0, exit_time=2000.0)
        tm.close_trade("T2", exit_price=105.0, exit_time=2000.0)

        assert len(gateway.close_orders) == 2
        closed_ids = {c["trade_id"] for c in gateway.close_orders}
        assert closed_ids == {"T1", "T2"}

    def test_strategy_close_while_executor_closing_dedup(self):
        """Rapid strategy closes for the same trade are deduped by the executor."""
        repo = FakeTradeRepository()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=None,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        tm = TradeManager(
            trade_repository=repo,
            socketio=DummySocketIO(),
            trade_executor=executor,
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        executor.trade_manager = tm

        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(tz=timezone.utc),
            trade_id="T1",
            account="Sim101",
        )

        executor.on_trade_close("T1", 90.0)
        assert len(gateway.close_orders) == 1

        executor.on_trade_close("T1", 90.0)
        assert len(gateway.close_orders) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
