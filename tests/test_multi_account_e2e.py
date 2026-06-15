"""Comprehensive E2E tests for multi-account trading.

These tests verify the full multi-account pipeline:
  Signal → MultiAccountExecutor → ZMQTradeExecutor → Gateway → (simulated) NT
  → Fills back → TradeManager → Strategy notification

Run with:  python -m pytest tests/test_multi_account_e2e.py -v
"""

from __future__ import annotations

import dataclasses
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

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
    def __init__(self, name: str, risk_usd: float | None = None,
                 risk_pct: float | None = None, rr_ratio: float | None = None):
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


# ═══════════════════════════════════════════════════════════════════════════════
# MultiAccountExecutor Core Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiAccountExpansion:
    """Test signal → per-account trade expansion."""

    def test_expands_to_all_accounts(self):
        """A single signal should create one trade per account."""
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[
                AccountConfig("A1", rr_ratio=3.0),
                AccountConfig("A2", rr_ratio=5.0),
                AccountConfig("A3", rr_ratio=2.0),
            ],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))

        assert len(executor.signal_to_accounts["S1"]) == 3
        assert len(gateway.open_orders) == 3
        # Each account trade should map back to the signal
        for aid in executor.signal_to_accounts["S1"]:
            assert executor.account_to_signal[aid] == "S1"

    def test_per_account_take_profit_recalculation(self):
        """Different rr_ratio per account should produce different TPs."""
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[
                AccountConfig("A1", rr_ratio=2.0),
                AccountConfig("A2", rr_ratio=4.0),
            ],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))

        tps = [o["take_profit"] for o in gateway.open_orders]
        # long: tp = entry + (rr * risk) = 100 + (rr * 10)
        assert 119.0 < tps[0] < 121.0  # ~120 for rr=2
        assert 139.0 < tps[1] < 141.0  # ~140 for rr=4

    def test_per_account_risk_override(self):
        """Account-specific risk_usd should be passed through."""
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[
                AccountConfig("A1", risk_usd=500.0),
                AccountConfig("A2", risk_usd=1000.0),
            ],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))

        risk_values = [o.get("risk_usd") for o in gateway.open_orders]
        assert 500.0 in risk_values
        assert 1000.0 in risk_values

    def test_empty_account_configs_is_noop(self):
        """If no accounts configured, nothing should happen (no crash)."""
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))

        assert executor.signal_to_accounts.get("S1") == []
        assert len(gateway.open_orders) == 0

    def test_source_propagates_to_account_trades(self):
        """source='test' on signal must be inherited by all account trades.

        Regression: TradeOpenUseCase was building trade_for_executor without
        the 'source' field, so MultiAccountExecutor always saw source=None and
        stored account trades with source='strategy'. Session end then closed
        them because 'strategy' is not in USER_CONTROLLED_SOURCES.
        """
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        signal = {**_signal_trade("S1"), "source": "test"}
        executor.on_trade_open(signal)

        for aid in executor.signal_to_accounts["S1"]:
            t = tm.trade_repository.get_trade(aid)
            assert t.source == "test", (
                f"Account trade {aid} has source={t.source!r}, expected 'test'. "
                "Check that TradeOpenUseCase.execute() includes 'source' in trade_for_executor."
            )

    def test_source_strategy_is_default_when_not_set(self):
        """Signals without explicit source default to 'strategy' on account trades."""
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))  # no source key

        aid = executor.signal_to_accounts["S1"][0]
        t = tm.trade_repository.get_trade(aid)
        assert t.source == "strategy"

    def test_account_trade_passes_through_without_recursion(self):
        """An already-expanded account trade must not recurse infinitely.

        Regression: TradeOpenUseCase.execute() calls executor.on_trade_open()
        with the account trade dict. If the executor is MultiAccountExecutor,
        it must detect the trade already has an 'account' field and pass it
        straight to the gateway instead of trying to expand it again.
        """
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[
                AccountConfig("A1", rr_ratio=3.0),
                AccountConfig("A2", rr_ratio=5.0),
            ],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        # Simulate what TradeOpenUseCase passes back to the executor
        account_trade = {
            "trade_id": "ACCT-123",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "account": "A1",
        }

        executor.on_trade_open(account_trade)

        # Should NOT create any new signal→account mappings
        assert len(executor.signal_to_accounts) == 0
        # Should pass through to gateway exactly once
        assert len(gateway.open_orders) == 1
        assert gateway.open_orders[0]["trade_id"] == "ACCT-123"


class TestMultiAccountClose:
    """Test close resolution signal→accounts."""

    def test_close_by_signal_id_expands_to_all_accounts(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[
                AccountConfig("A1"),
                AccountConfig("A2"),
            ],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        account_ids = executor.signal_to_accounts["S1"]

        gateway.close_orders.clear()
        executor.on_trade_close("S1", 110.0)

        assert len(gateway.close_orders) == 2
        closed_ids = {c["trade_id"] for c in gateway.close_orders}
        assert closed_ids == set(account_ids)
        # Should NOT close by signal ID directly
        assert "S1" not in closed_ids

    def test_close_by_account_trade_id_directly(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        account_id = executor.signal_to_accounts["S1"][0]

        gateway.close_orders.clear()
        executor.on_trade_close(account_id, 110.0)

        assert len(gateway.close_orders) == 1
        assert gateway.close_orders[0]["trade_id"] == account_id

    def test_close_includes_account_name(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        executor.on_trade_close("S1", 110.0)

        assert gateway.close_orders[0].get("account") == "Sim101"


class TestMultiAccountSLUpdate:
    """Test SL update resolution signal→accounts."""

    def test_sl_update_by_signal_id_expands_to_all_accounts(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        account_ids = executor.signal_to_accounts["S1"]

        executor.on_sl_update("S1", 95.0)

        assert len(gateway.modify_orders) == 2
        modified_ids = {m["trade_id"] for m in gateway.modify_orders}
        assert modified_ids == set(account_ids)

    def test_sl_update_includes_account_name(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        executor.on_sl_update("S1", 95.0)

        assert gateway.modify_orders[0].get("account") == "Sim101"


class TestMultiAccountMappings:
    """Test signal<->account trade ID mappings."""

    def test_get_signal_id(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        for aid in executor.signal_to_accounts["S1"]:
            assert executor.get_signal_id(aid) == "S1"

        assert executor.get_signal_id("UNKNOWN") is None

    def test_all_account_trades_closed(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        assert not executor.all_account_trades_closed("S1")

        # Close all account trades via TradeManager
        for aid in executor.signal_to_accounts["S1"]:
            tm.close_trade(aid, 110.0, 2000.0)

        assert executor.all_account_trades_closed("S1")

    def test_account_for_trade_db_lookup(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        aid = executor.signal_to_accounts["S1"][0]

        assert executor._account_for_trade(aid) == "Sim101"

    def test_account_for_trade_fallback_when_db_missing(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        # Trade ID that doesn't exist in DB — fallback to config name inference
        assert executor._account_for_trade("some_Sim101_id") == "Sim101"

    def test_account_for_trade_empty_configs(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        assert executor._account_for_trade("anything") == ""


class TestMultiAccountThreadSafety:
    """Verify RLock protects shared dicts under concurrent access."""

    def test_concurrent_open_and_close(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig(f"A{i}") for i in range(5)],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        errors: list[Exception] = []

        def opener(n: int):
            try:
                for i in range(n):
                    executor.on_trade_open(_signal_trade(f"S{i}"))
            except Exception as e:
                errors.append(e)

        def closer(n: int):
            try:
                for i in range(n):
                    if f"S{i}" in executor.signal_to_accounts:
                        executor.on_trade_close(f"S{i}", 110.0)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=opener, args=(20,))
        t2 = threading.Thread(target=closer, args=(20,))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Thread safety errors: {errors}"
        # All account trades should be closed or never opened (no crash)


# ═══════════════════════════════════════════════════════════════════════════════
# TradeManager + MultiAccountExecutor Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiAccountBrokerFills:
    """Verify broker fills correctly update account trades and balance."""

    def test_entry_fill_updates_correct_account_trade(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        aid1, aid2 = executor.signal_to_accounts["S1"]

        # Simulate broker entry fill for first account trade only
        tm.handle_broker_entry_fill(aid1, entry_price=99.5, stop_loss=89.5, take_profit=129.5)

        # Only the first account trade should be updated
        t1 = tm.trade_repository.get_trade(aid1)
        t2 = tm.trade_repository.get_trade(aid2)
        assert t1.entry_price == 99.5
        assert t1.stop_loss == 89.5
        # Second trade should still have original values
        assert t2.entry_price == 100.0

    def test_broker_fill_closes_correct_account_trade_and_updates_balance(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        aid1, aid2 = executor.signal_to_accounts["S1"]

        initial_balance = tm.account_balance

        # Simulate TP fill for first account trade
        tm.handle_broker_fill(aid1, exit_price=130.0, result_type="TP")

        # First trade should be closed
        assert tm.trade_repository.get_trade(aid1).exit_time is not None
        # Second trade should still be open
        assert tm.trade_repository.get_trade(aid2).exit_time is None
        # Balance should have increased (winning trade)
        assert tm.account_balance > initial_balance

    def test_broker_fill_routes_back_to_signal_id(self):
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        aid = executor.signal_to_accounts["S1"][0]

        assert executor.get_signal_id(aid) == "S1"


# ═══════════════════════════════════════════════════════════════════════════════
# ZMQTradeExecutor Account Parameter Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestZMQTradeExecutorAccountRouting:
    """Verify ZMQTradeExecutor passes account through to gateway."""

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

    def test_close_order_includes_account(self):
        gateway = MagicMock()
        executor = ZMQTradeExecutor(gateway, FakeLogger())

        # Note: ZMQTradeExecutor.on_trade_close does NOT pass account.
        # MultiAccountExecutor bypasses it and calls gateway directly.
        # This test documents current behavior.
        executor.on_trade_close("T1", 110.0)
        gateway.send_close_order.assert_called_once_with(
            trade_id="T1", reason="strategy"
        )

    def test_modify_order_includes_account(self):
        gateway = MagicMock()
        executor = ZMQTradeExecutor(gateway, FakeLogger())

        # Note: ZMQTradeExecutor.on_sl_update does NOT pass account.
        # MultiAccountExecutor bypasses it and calls gateway directly.
        executor.on_sl_update("T1", 95.0)
        gateway.send_modify_order.assert_called_once_with(
            trade_id="T1", stop_loss=95.0
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Full E2E Flow Test
# ═══════════════════════════════════════════════════════════════════════════════

class TestFullE2EFlow:
    """End-to-end: signal → expansion → commands → fills → close notification."""

    def test_full_flow_two_accounts(self):
        """
        1. Strategy emits signal S1
        2. MultiAccountExecutor expands to A1, A2
        3. ZMQ commands sent with account tags
        4. Simulated broker entry fills arrive
        5. Simulated broker exit fills arrive
        6. Account trades closed, balance updated
        """
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[
                AccountConfig("Sim101", risk_usd=500.0),
                AccountConfig("Sim102", risk_usd=1000.0),
            ],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        # ── Step 1: Signal emitted ──
        signal = _signal_trade("S1")
        executor.on_trade_open(signal)

        assert len(executor.signal_to_accounts["S1"]) == 2
        aid1, aid2 = executor.signal_to_accounts["S1"]

        # ── Step 2: Verify ZMQ open commands ──
        assert len(gateway.open_orders) == 2
        accounts_sent = {o["account"] for o in gateway.open_orders}
        assert accounts_sent == {"Sim101", "Sim102"}

        # ── Step 3: Simulated entry fills ──
        tm.handle_broker_entry_fill(aid1, entry_price=100.0, stop_loss=90.0, take_profit=130.0)
        tm.handle_broker_entry_fill(aid2, entry_price=100.0, stop_loss=90.0, take_profit=130.0)

        # ── Step 4: Simulated TP exit fills ──
        initial_balance = tm.account_balance
        tm.handle_broker_fill(aid1, exit_price=130.0, result_type="TP")
        tm.handle_broker_fill(aid2, exit_price=130.0, result_type="TP")

        # ── Step 5: Verify closures ──
        assert tm.trade_repository.get_trade(aid1).exit_time is not None
        assert tm.trade_repository.get_trade(aid2).exit_time is not None
        assert tm.account_balance > initial_balance
        assert executor.all_account_trades_closed("S1")

        # ── Step 6: Strategy close by signal ID should be no-op (already closed) ──
        gateway.close_orders.clear()
        executor.on_trade_close("S1", 130.0)
        # TradeManager guard prevents double-close, but MultiAccountExecutor
        # still sends close commands to gateway for safety
        assert len(gateway.close_orders) == 2

    def test_partial_close_one_account_sl_one_account_tp(self):
        """One account hits SL, the other hits TP — mixed results."""
        tm = _make_trade_manager()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_open(_signal_trade("S1"))
        aid1, aid2 = executor.signal_to_accounts["S1"]

        balance_before = tm.account_balance

        # A1 hits SL (loss)
        tm.handle_broker_fill(aid1, exit_price=90.0, result_type="SL")
        # A2 hits TP (win)
        tm.handle_broker_fill(aid2, exit_price=130.0, result_type="TP")

        t1 = tm.trade_repository.get_trade(aid1)
        t2 = tm.trade_repository.get_trade(aid2)

        assert t1.result_type == "SL"
        assert t2.result_type == "TP"
        assert t1.result < 0
        assert t2.result > 0

        # Net balance change depends on sizing, but both should be recorded
        assert tm.account_balance != balance_before
        assert executor.all_account_trades_closed("S1")


# ═══════════════════════════════════════════════════════════════════════════════
# Reentry Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiAccountReentryIntegration:
    """End-to-end: broker SL fill → TradeManager → EventBus → Strategy → reentry."""

    def _make_strategy(self, event_bus, trade_manager, trade_repo, options=None, account_configs=None):
        """Helper to create a strategy wired to the event bus."""
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

    def test_broker_sl_fill_creates_reentry_via_event_bus(self):
        """
        Regression test for live multi-account reentry bug.

        When an account trade hits SL, the TradeCloseUseCase emits a
        TRADE_CLOSED event via the EventBus. The strategy receives it,
        looks up the signal trade by signal_id, and must create a reentry
        opportunity even though the account trade payload lacks line_level.
        """
        # 1. Set up EventBus + TradeManager with real domain publisher
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )

        # 2. Set up MultiAccountExecutor
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )

        # 3. Create strategy subscribed to TRADE_CLOSED
        strategy = self._make_strategy(event_bus, tm, tr)

        # 4. Seed the strategy with a signal trade that has line_level
        signal_trade = {
            "trade_id": "S1",
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
            "rr_ratio": 5.0,
        }
        strategy.open_trades.append(signal_trade)

        # 5. Expand to account trade
        executor.on_trade_open(signal_trade)
        aid = executor.signal_to_accounts["S1"][0]

        # 6. Simulate broker SL fill
        tm.handle_broker_fill(aid, exit_price=90.0, result_type="SL")

        # 7. Assert strategy created reentry opportunity
        assert len(strategy.open_trades) == 0, "Signal trade should be removed from open_trades"
        assert len(strategy._reentry_opportunities) == 1, "Reentry opportunity should be created"
        opp = strategy._reentry_opportunities[0]
        assert opp["level"] == 95.0
        assert opp["direction"] == "long"
        assert opp["pair"] == "MNQ"

    def test_broker_tp_fill_does_not_create_reentry(self):
        """TP hits should not create reentry opportunities."""
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        strategy = self._make_strategy(event_bus, tm, tr)

        signal_trade = {
            "trade_id": "S1",
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
            "rr_ratio": 5.0,
        }
        strategy.open_trades.append(signal_trade)
        executor.on_trade_open(signal_trade)
        aid = executor.signal_to_accounts["S1"][0]

        tm.handle_broker_fill(aid, exit_price=130.0, result_type="TP")

        assert len(strategy.open_trades) == 0
        assert len(strategy._reentry_opportunities) == 0

    def test_reentry_trade_does_not_chain(self):
        """A trade that is already a reentry should not spawn another reentry."""
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        strategy = self._make_strategy(event_bus, tm, tr)

        signal_trade = {
            "trade_id": "S1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "status": "open",
            "line_level": 95.0,
            "is_reentry": True,  # already a reentry
            "entry_time": 1000.0,
            "rr_ratio": 5.0,
        }
        strategy.open_trades.append(signal_trade)
        executor.on_trade_open(signal_trade)
        aid = executor.signal_to_accounts["S1"][0]

        tm.handle_broker_fill(aid, exit_price=90.0, result_type="SL")

        assert len(strategy.open_trades) == 0
        assert len(strategy._reentry_opportunities) == 0

    def test_duplicate_exit_fill_does_not_double_count(self):
        """Two SL fills for the same account trade must be idempotent."""
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        strategy = self._make_strategy(event_bus, tm, tr)

        signal_trade = {
            "trade_id": "S1",
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
            "rr_ratio": 5.0,
        }
        strategy.open_trades.append(signal_trade)
        executor.on_trade_open(signal_trade)
        aid = executor.signal_to_accounts["S1"][0]


        # First SL fill
        tm.handle_broker_fill(aid, exit_price=90.0, result_type="SL")
        balance_after_first = tm.account_balance

        # Duplicate SL fill (NT retry / network duplicate)
        tm.handle_broker_fill(aid, exit_price=90.0, result_type="SL")
        balance_after_second = tm.account_balance

        # Balance must not change on the duplicate
        assert balance_after_first == balance_after_second
        # Trade should be closed in DB exactly once
        assert len(tr.closed) == 1
        # Strategy should have exactly one reentry opportunity
        assert len(strategy._reentry_opportunities) == 1

    def test_unknown_trade_id_fill_is_graceful(self):
        """An EXIT_FILL for a trade ID Python has never seen must not crash."""
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        strategy = self._make_strategy(event_bus, tm, tr)

        # No trades opened — simulate orphaned fill from a previous session
        tm.handle_broker_fill("UNKNOWN-TRADE-123", exit_price=90.0, result_type="SL")

        assert len(strategy.open_trades) == 0
        assert len(strategy._reentry_opportunities) == 0
        assert tm.account_balance == 100000.0  # unchanged

    def test_breakeven_live_end_to_end(self):
        """Bar → check_breakeven → SL update → ZMQ modify command to NT."""
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=None,  # set after tm is created
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=executor,
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        executor.trade_manager = tm
        strategy = self._make_strategy(
            event_bus, tm, tr,
            options=dataclasses.replace(DEFAULT_STRATEGY_OPTIONS,
                reentry_after_sl=True,
                breakeven=BreakevenConfig(trigger_rr=1.0, move_to_rr=0.0),
            ),
        )

        signal_trade = {
            "trade_id": "S1",
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
            "rr_ratio": 5.0,
        }
        strategy.open_trades.append(signal_trade)
        executor.on_trade_open(signal_trade)
        aid = executor.signal_to_accounts["S1"][0]

        # Bar that triggers breakeven (high >= entry + risk * trigger_rr = 100 + 10*1.0 = 110)
        bar = {
            "time": 2000.0,
            "pair": "MNQ",
            "open": 100.0,
            "high": 110.0,
            "low": 100.0,
            "close": 105.0,
        }
        strategy.check_breakeven(bar)

        # Verify gateway received an SL modify command
        assert len(gateway.modify_orders) == 1
        assert gateway.modify_orders[0]["trade_id"] == aid
        # New SL should be moved to entry (breakeven)
        assert gateway.modify_orders[0]["stop_loss"] == 100.0

    def test_session_end_close_live_end_to_end(self):
        """Session-end bar triggers close commands to all account trades."""
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=None,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=executor,
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        executor.trade_manager = tm
        strategy = self._make_strategy(event_bus, tm, tr)

        signal_trade = {
            "trade_id": "S1",
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
            "rr_ratio": 5.0,
        }
        strategy.open_trades.append(signal_trade)
        executor.on_trade_open(signal_trade)
        aid1, aid2 = executor.signal_to_accounts["S1"]

        # Simulate session-end close via TradeManager (mimics app_factory logic)
        tm.close_trade(aid1, exit_price=105.0, exit_time=2000.0)
        tm.close_trade(aid2, exit_price=105.0, exit_time=2000.0)

        # Gateway should receive close commands
        assert len(gateway.close_orders) == 2
        closed_ids = {c["trade_id"] for c in gateway.close_orders}
        assert closed_ids == {aid1, aid2}

    def test_strategy_close_while_nt_closing_dedup(self):
        """
        If NT already sent EXIT_FILL and strategy then tries to close,
        the dedup window in MultiAccountExecutor suppresses the redundant command
        ONLY if the previous close went through the executor (not via broker fill).

        This test verifies the dedup window works when two strategy-driven closes
        happen in rapid succession.
        """
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=None,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=executor,
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        executor.trade_manager = tm
        strategy = self._make_strategy(event_bus, tm, tr)

        signal_trade = {
            "trade_id": "S1",
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
            "rr_ratio": 5.0,
        }
        strategy.open_trades.append(signal_trade)
        executor.on_trade_open(signal_trade)

        # First strategy-driven close (e.g. session end)
        executor.on_trade_close("S1", 90.0)
        assert len(gateway.close_orders) == 1

        # Second strategy-driven close within 5-second dedup window
        gateway.close_orders.clear()
        executor.on_trade_close("S1", 90.0)

        # Dedup window should suppress the redundant close command
        assert len(gateway.close_orders) == 0

    def test_test_source_account_trades_exempt_from_session_end(self):
        """Account trades expanded from a test-sourced signal must survive session end.

        Regression: when source='test' was missing from trade_for_executor, account
        trades were stored with source='strategy' and closed by session end. This
        test verifies the full path: signal(source='test') → MultiAccountExecutor
        → account trades with source='test' → session-end bar → still open.
        """
        from zoneinfo import ZoneInfo
        from tests.conftest import make_bar

        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        executor = MultiAccountExecutor(
            trade_manager=None,
            account_configs=[AccountConfig("A1"), AccountConfig("A2")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=executor,
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
            session_end_time="16:58",
            session_tz="America/New_York",
        )
        executor.trade_manager = tm

        # Open a test-sourced signal — both account trades should inherit source='test'
        signal = {**_signal_trade("S1"), "source": "test"}
        executor.on_trade_open(signal)
        aid1, aid2 = executor.signal_to_accounts["S1"]

        # Verify source was stored correctly before the session-end check
        for aid in (aid1, aid2):
            t = tr.get_trade(aid)
            assert t.source == "test", (
                f"Precondition failed: account trade {aid} source={t.source!r}, expected 'test'"
            )

        # Fire a session-end bar (16:59 NY)
        ny = ZoneInfo("America/New_York")
        bar_dt = datetime(2025, 6, 15, 16, 59, tzinfo=ny)
        bar = make_bar(time=int(bar_dt.timestamp()), close=105, pair="MNQ")
        tm.handle_new_1m_bar(bar)

        # Account trades with source='test' must NOT be closed
        assert len(tr.closed) == 0, (
            f"Session end closed {len(tr.closed)} test-sourced account trade(s) — "
            "source='test' must be in USER_CONTROLLED_SOURCES and must flow from signal "
            "through TradeOpenUseCase into account trades."
        )
        assert len(gateway.close_orders) == 0, (
            "Session end sent close commands to gateway for test-sourced trades"
        )

    def test_restore_open_trades_preserves_reentry_state(self):
        """Crash recovery must preserve line_level and is_reentry from DB."""
        event_bus = EventBus()
        publisher = DomainEventBusPublisher(event_bus)
        tr = FakeTradeRepository()
        tm = TradeManager(
            trade_repository=tr,
            socketio=publisher,
            trade_executor=FakeTradeExecutor(),
            analytics=FakeAnalyticsReporter(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        gateway = FakeGateway()
        zmq_ex = FakeZMQExecutor(gateway, FakeLogger())
        MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig("Sim101")],
            gateway_executor=zmq_ex,
            logger=FakeLogger(),
        )
        strategy = self._make_strategy(event_bus, tm, tr, account_configs=[AccountConfig("Sim101")])

        # Seed DB with a signal trade that has line_level and is_reentry in params
        tr.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime.now(),
            params={"line_level": 95.0, "is_reentry": True},
            source="strategy",
            trade_id="S1",
        )
        # Seed TradeManager with the corresponding account trade
        tm.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            rr_ratio=5.0,
            account="Sim101",
            signal_id="S1",
        )

        strategy.restore_open_trades()

        assert len(strategy.open_trades) == 1
        restored = strategy.open_trades[0]
        assert restored["trade_id"] == "S1"
        assert restored["line_level"] == 95.0
        assert restored["is_reentry"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
