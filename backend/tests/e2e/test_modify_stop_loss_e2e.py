"""End-to-end test for stop-loss modification.

Opens a TEST Long through the real controller wiring in live mode, calls the
modify-stop-loss endpoint, and asserts that an ORDER_MODIFY command is sent to
the platform and the local trade record is updated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest


@dataclass
class _ModifySlHarness:
    nt: Any
    app: Any


@pytest.fixture
def modify_sl_harness(e2e_harness_multi):
    app = e2e_harness_multi.app
    nt = e2e_harness_multi.nt

    # Seed the 1m buffer so PriceResolver returns a deterministic price.
    app.loader._1m_buffer.append({
        "time": int(time.time()) - 60,
        "open": 30155.0,
        "high": 30158.0,
        "low": 30154.0,
        "close": 30156.0,
        "volume": 100,
        "pair": "MNQ",
    })

    yield _ModifySlHarness(nt=nt, app=app)


class TestModifyStopLossE2E:
    def test_modify_stop_loss_sends_modify_command(self, modify_sl_harness: _ModifySlHarness) -> None:
        nt = modify_sl_harness.nt
        app = modify_sl_harness.app

        # 1) Open a TEST Long via the controller (same path as the UI button).
        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        # 2) Wait for the order to reach NinjaTrader.
        open_command = nt.wait_for_command("order_open", timeout=5.0)
        assert open_command["payload"]["trade_id"] == trade_id

        # 3) Modify the stop loss through the controller.
        new_sl = 30136.0
        with app.app.app_context():
            response = app.trades_controller.modify_stop_loss(trade_id, new_sl)
        assert response[1] == 200
        assert response[0].json["stop_loss"] == new_sl

        # 4) Wait for the modify command to reach NinjaTrader.
        modify_command = nt.wait_for_command("order_modify", timeout=5.0)
        payload = modify_command["payload"]
        assert payload["trade_id"] == trade_id
        assert payload["stop_loss"] == new_sl

        # 5) The local trade record should reflect the new SL.
        with app.app.app_context():
            db_trade = app.trade_manager.trade_repository.get_trade(trade_id)
        assert db_trade is not None
        assert db_trade.stop_loss == new_sl
