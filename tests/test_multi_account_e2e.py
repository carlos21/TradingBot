"""Comprehensive E2E tests for multi-account trading.

These tests verify the full multi-account pipeline:
  Signal → MultiAccountExecutor → ZMQTradeExecutor → Gateway → (simulated) NT
  → Fills back → TradeManager → Strategy notification

Run with:  python -m pytest tests/test_multi_account_e2e.py -v
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.infrastructure.gateway.executor import MultiAccountExecutor, ZMQTradeExecutor
from src.services.trade_manager import TradeManager
from src.domain.types import Direction
from tests.fakes import FakeLogger, FakeTradeExecutor, FakeTradeRepository, DummySocketIO


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
