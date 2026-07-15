"""End-to-end test covering a full trade lifecycle through the ZMQ path.

Open → entry fill → modify stop-loss → manual close, verifying that each
command reaches the fake NinjaTrader and that the trade repository reflects
the final closed state.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.e2e.conftest import E2EHarness


def _wait_for_trade_in_tm(trade_manager, trade_id: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t in trade_manager.open_trades:
            if t["trade_id"] == trade_id:
                return t
        time.sleep(0.01)
    raise TimeoutError(f"Trade {trade_id} not found in trade_manager.open_trades")


def _wait_for_trade_closed_in_repo(trade_repo, trade_id: str, timeout: float = 5.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        trade = trade_repo.get_trade(trade_id)
        if trade and trade.exit_time is not None:
            return trade
        time.sleep(0.01)
    raise TimeoutError(f"Trade {trade_id} not closed in repository")


class TestFullTradeLifecycleE2E:
    def test_open_fill_modify_sl_and_close(self, e2e_harness: E2EHarness) -> None:
        app = e2e_harness.app
        nt = e2e_harness.nt

        trade = app.trade_manager.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=21000.0,
            stop_loss=20920.0,
            take_profit=21200.0,
            risk=80.0,
            entry_time=time.time(),
            rr_ratio=3.3,
        )
        trade_id = trade["trade_id"]

        # 1) Broker opens the position.
        open_cmd = nt.wait_for_command("order_open")
        assert open_cmd["payload"]["trade_id"] == trade_id
        nt.simulate_entry_fill(trade_id, entry_price=21000.0)
        tm_trade = _wait_for_trade_in_tm(app.trade_manager, trade_id)
        assert tm_trade["entry"] == pytest.approx(21000.0)

        # 2) Move stop-loss to breakeven through the controller.
        with app.app.app_context():
            resp = app.trades_controller.modify_stop_loss(trade_id, 21000.0)
        assert resp[1] == 200
        assert resp[0].json["stop_loss"] == pytest.approx(21000.0)

        sl_cmd = nt.wait_for_command("order_modify")
        assert sl_cmd["payload"]["trade_id"] == trade_id
        assert float(sl_cmd["payload"]["stop_loss"]) == pytest.approx(21000.0)

        # 3) Manually close the trade and verify the close command + repo state.
        app.loader._last_bar_close = 21100.0
        with app.app.app_context():
            close_resp = app.trades_controller.close_trade(trade_id)
        assert close_resp[1] == 200

        close_cmd = nt.wait_for_command("order_close")
        assert close_cmd["payload"]["trade_id"] == trade_id

        nt.simulate_exit_fill(trade_id, exit_price=21100.0, result_type="CLOSE")
        closed = _wait_for_trade_closed_in_repo(app.trade_manager.trade_repository, trade_id)
        assert closed.result_type == "CLOSE"
        assert closed.exit_price == pytest.approx(21100.0)
