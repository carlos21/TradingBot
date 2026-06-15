"""End-to-end test for broker entry fill price propagation.

Opens a TEST Long through the real controller wiring in multi-account live
mode, simulates a NinjaTrader fill at a different price, and asserts that the
parent signal trade is updated in the database while the browser only receives
the account trade event (parent signal events are filtered from the UI).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest


class _EmitCatcher:
    """Records Socket.IO emit calls for assertions."""

    def __init__(self, socketio):
        self._socketio = socketio
        self.events: list[tuple[str, Any]] = []
        self._orig_emit = socketio.emit
        socketio.emit = self._emit

    def _emit(self, event, *args, **kwargs):
        payload = args[0] if args else kwargs.get("data")
        self.events.append((event, payload))
        return self._orig_emit(event, *args, **kwargs)

    def restore(self):
        self._socketio.emit = self._orig_emit


@dataclass
class _FillHarness:
    nt: Any
    app: Any
    catcher: _EmitCatcher


@pytest.fixture
def fill_harness(e2e_harness_multi):
    app = e2e_harness_multi.app
    nt = e2e_harness_multi.nt

    # Seed the 1m buffer so PriceResolver returns a deterministic requested entry.
    app.loader._1m_buffer.append({
        "time": int(time.time()) - 60,
        "open": 30155.0,
        "high": 30158.0,
        "low": 30154.0,
        "close": 30156.0,
        "volume": 100,
        "pair": "MNQ",
    })

    catcher = _EmitCatcher(app.socketio)
    yield _FillHarness(nt=nt, app=app, catcher=catcher)
    catcher.restore()


class TestEntryFillE2E:
    def test_entry_fill_updates_parent_signal_trade(self, fill_harness: _FillHarness) -> None:
        nt = fill_harness.nt
        app = fill_harness.app

        # 1) Open a TEST Long via the controller (same path as the UI button).
        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        signal_trade_id = response[0].json["trade_id"]

        # 2) Wait for the per-account order(s) to reach NinjaTrader.
        commands = nt.wait_for_command_count(count=2, timeout=5.0)
        open_commands = [c for c in commands if c["msg_type"] == "order_open"]
        assert len(open_commands) >= 1
        account_trade_id = open_commands[0]["payload"]["trade_id"]

        # 3) Simulate NT entry fill at a different price than requested.
        nt.simulate_entry_fill(account_trade_id, entry_price=30157.27)

        # 4) Give the gateway callback time to process the fill.
        deadline = time.time() + 2.0
        db_signal = None
        while time.time() < deadline:
            db_signal = app.trade_manager.trade_repository.get_trade(signal_trade_id)
            if db_signal and db_signal.entry_price == 30157.27:
                break
            time.sleep(0.01)

        assert db_signal is not None
        assert db_signal.entry_price == 30157.27

        # 5) The account trade DB record should also reflect the real fill.
        db_account = app.trade_manager.trade_repository.get_trade(account_trade_id)
        assert db_account is not None
        assert db_account.entry_price == 30157.27

        # 6) Frontend should receive trade_entry_update only for the account trade.
        #    The parent signal event is filtered by SocketIOBridge so the UI shows
        #    exactly one row per account.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            updates = [e for e in fill_harness.catcher.events if e[0] == "trade_entry_update"]
            trade_ids = {e[1]["trade_id"] for e in updates}
            if account_trade_id in trade_ids:
                break
            time.sleep(0.01)

        updates = [e for e in fill_harness.catcher.events if e[0] == "trade_entry_update"]
        trade_ids = {e[1]["trade_id"] for e in updates}
        assert account_trade_id in trade_ids
        assert signal_trade_id not in trade_ids
        for event, payload in updates:
            if payload["trade_id"] == account_trade_id:
                assert payload["entry_price"] == 30157.27
