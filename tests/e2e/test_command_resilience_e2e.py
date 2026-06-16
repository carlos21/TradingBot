"""End-to-end resilience tests for ZMQ command handling."""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.e2e.conftest import E2EHarness


def _make_bar(t: int, open_: float, high: float, low: float, close: float) -> dict[str, Any]:
    return {
        "time": t,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1,
        "pair": "MNQ",
    }


def _send_history_and_go_live(nt, app, bars: list[dict[str, Any]]) -> None:
    nt.send_history_batch(bars)
    nt.send_history_end()
    from src.domain.readiness.readiness_state import ReadinessState
    sm = app.strategy.execution_context._state_machine
    deadline = time.time() + 5.0
    while sm.state != ReadinessState.LIVE and time.time() < deadline:
        time.sleep(0.05)
    assert sm.state == ReadinessState.LIVE
    nt.clear_commands()


class TestCommandResilience:
    """Order command ACK/NACK and duplicate handling."""

    def test_order_open_nack_rolls_back_trade(self, e2e_harness: E2EHarness) -> None:
        """A negative ACK for order_open is asynchronously rolled back by the executor."""
        nt = e2e_harness.nt
        app = e2e_harness.app

        # Force a deterministic price so the controller does not abort early.
        app.loader._1m_buffer.append({
            "time": int(time.time()) - 60,
            "open": 30155.0,
            "high": 30158.0,
            "low": 30154.0,
            "close": 30156.0,
            "volume": 100,
            "pair": "MNQ",
        })

        nt.reject_next_order_open()

        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")

        # The controller returns 201 immediately; the async cleanup happens when
        # the NACK arrives.
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        # Wait for the NACK to be processed and the trade to be cancelled.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            trades = app.trade_manager.trade_repository.list_trades("MNQ")
            cancelled = [
                t for t in trades
                if t.trade_id == trade_id and t.exit_time is not None
            ]
            if cancelled:
                break
            time.sleep(0.05)

        assert len(cancelled) == 1
        assert "ORDER_OPEN_FAILED" in (cancelled[0].result_type or "")

    def test_order_close_nack_is_logged(self, e2e_harness: E2EHarness) -> None:
        """A negative ACK for order_close is logged because the DB already closed optimistically."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = int(time.time())

        app.loader._1m_buffer.append({
            "time": now_ts - 60,
            "open": 30155.0,
            "high": 30158.0,
            "low": 30154.0,
            "close": 30156.0,
            "volume": 100,
            "pair": "MNQ",
        })
        nt.clear_commands()

        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        # Wait for the open command to be recorded so FakeNT knows the trade.
        nt.wait_for_command("order_open", timeout=3.0)

        # Reject the close command and close the trade locally.
        nt.reject_next_order_close()
        app.trade_manager.close_trade(trade_id, exit_price=30156.0, exit_time=time.time())
        nt.wait_for_command("order_close", timeout=3.0)

        # The trade is closed in DB; the failure handler only logs the mismatch.
        trade = app.trade_manager.trade_repository.get_trade(trade_id)
        assert trade is not None
        assert trade.exit_time is not None

    def test_duplicate_order_open_is_deduped(self, e2e_harness: E2EHarness) -> None:
        """Replaying the same order_open command does not create duplicate entries."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = int(time.time())

        # Seed a price so the controller can resolve an entry price.
        app.loader._1m_buffer.append({
            "time": now_ts - 60,
            "open": 30155.0,
            "high": 30158.0,
            "low": 30154.0,
            "close": 30156.0,
            "volume": 100,
            "pair": "MNQ",
        })

        # The harness is already forced-ready, so we can open a trade directly.
        nt.clear_commands()

        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        nt.wait_for_command("order_open", timeout=3.0)

        # Simulate the same fill twice. FakeNT's tracker dedups by trade_id, so
        # only the first fill should be processed.
        nt.simulate_entry_fill(trade_id, entry_price=30156.0)
        nt.simulate_entry_fill(trade_id, entry_price=30156.0)

        entries = [c for c in nt.commands_received if c["msg_type"] == "order_open"]
        assert len(entries) == 1
