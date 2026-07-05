"""End-to-end test for broker entry fill price propagation.

Opens a TEST Long through the real controller wiring in live mode, simulates a
NinjaTrader fill at a different price, and asserts that the trade DB record is
updated and the frontend receives the entry-update event.
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
    def test_entry_fill_updates_trade_record(self, fill_harness: _FillHarness) -> None:
        nt = fill_harness.nt
        app = fill_harness.app

        # 1) Open a TEST Long via the controller (same path as the UI button).
        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        # 2) Wait for the order to reach NinjaTrader.
        open_commands = [nt.wait_for_command("order_open", timeout=5.0)]
        assert open_commands[0]["payload"]["trade_id"] == trade_id

        # 3) Simulate NT entry fill at a different price than requested.
        nt.simulate_entry_fill(trade_id, entry_price=30157.27)

        # 4) Give the gateway callback time to process the fill.
        deadline = time.time() + 2.0
        db_trade = None
        while time.time() < deadline:
            db_trade = app.trade_manager.trade_repository.get_trade(trade_id)
            if db_trade and db_trade.entry_price == 30157.27:
                break
            time.sleep(0.01)

        assert db_trade is not None
        assert db_trade.entry_price == 30157.27

        # 5) The frontend should receive a trade_entry_update event.
        updates = [e for e in fill_harness.catcher.events if e[0] == "trade_entry_update"]
        assert any(e[1]["trade_id"] == trade_id for e in updates)
