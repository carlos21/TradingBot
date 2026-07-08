"""E2E regression test for the 2026-07-07 MNQ position-sizing incident.

Verifies that in live mode Python forwards the *configured* risk field to
NinjaTrader (risk_usd when fixed-dollar risk is configured, risk_pct when
percent risk is configured), and that the broker-reported fill quantity is
written back to the DB record.
"""

from __future__ import annotations

import time
from typing import Any

import pytest


@pytest.fixture
def risk_harness(e2e_harness_multi):
    """Shortcut to the multi-account harness used by these tests."""
    harness = e2e_harness_multi
    # Seed the 1m buffer so PriceResolver returns a deterministic requested entry.
    harness.app.loader._1m_buffer.append({
        "time": int(time.time()) - 60,
        "open": 30155.0,
        "high": 30158.0,
        "low": 30154.0,
        "close": 30156.0,
        "volume": 100,
        "pair": "MNQ",
    })
    yield harness


class TestLiveRiskPayload:
    def test_order_open_sends_configured_risk_usd(self, risk_harness: Any) -> None:
        """When the account is configured with fixed-dollar risk, send risk_usd."""
        app = risk_harness.app
        nt = risk_harness.nt

        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        command = nt.wait_for_command("order_open", timeout=5.0)
        payload = command["payload"]
        assert payload["trade_id"] == trade_id
        assert payload["direction"] == "long"
        # Multi-account harness configures Sim101 with risk_usd=500.0
        assert payload.get("risk_usd") == 500.0, (
            "Configured fixed-dollar risk must be forwarded to NinjaTrader"
        )
        assert payload.get("risk_pct") is None, (
            "risk_pct must be absent when risk_usd is configured"
        )

    def test_order_open_sends_configured_risk_pct(self, risk_harness: Any) -> None:
        """When the account is configured with %-risk and no fixed risk, send risk_pct."""
        app = risk_harness.app
        nt = risk_harness.nt

        # Remove the fixed-dollar risk from Sim101 so only risk_pct remains.
        app.trade_manager._accounts_repo.upsert(
            "Sim101", risk_usd=None, risk_pct=1.6
        )

        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        command = nt.wait_for_command("order_open", timeout=5.0)
        payload = command["payload"]
        assert payload["trade_id"] == trade_id
        assert payload["direction"] == "long"
        assert payload.get("risk_usd") is None, (
            "risk_usd must be absent when only risk_pct is configured"
        )
        assert payload.get("risk_pct") == 1.6, (
            "Configured percent risk must be forwarded to NinjaTrader"
        )

    def test_entry_fill_quantity_updates_db_contracts(self, risk_harness: Any) -> None:
        """Broker-reported quantity overrides any Python-side estimate."""
        app = risk_harness.app
        nt = risk_harness.nt

        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        nt.wait_for_command("order_open", timeout=5.0)

        # Fake NT reports the entry was filled with a different quantity than
        # Python might have estimated.
        nt.simulate_entry_fill(trade_id, entry_price=30157.25, quantity=7)

        deadline = time.time() + 2.0
        db_trade = None
        while time.time() < deadline:
            db_trade = app.trade_manager.trade_repository.get_trade(trade_id)
            if db_trade and db_trade.contracts == 7:
                break
            time.sleep(0.01)

        assert db_trade is not None
        assert db_trade.contracts == 7
        assert db_trade.entry_price == 30157.25

    def test_partial_entry_fills_accumulate_contracts(self, risk_harness: Any) -> None:
        """Several partial ENTRY_FILL messages accumulate to the total size."""
        app = risk_harness.app
        nt = risk_harness.nt

        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        cmd = nt.wait_for_command("order_open", timeout=5.0)
        payload = cmd["payload"]
        # The fake sizes exactly like real NT: risk_usd / (risk_points * point_value).
        expected_contracts = max(
            1,
            int(round(payload["risk_usd"] / (payload["risk_points"] * 2.0))),
        )

        # Simulate the market order being filled in several chunks.
        fills = [
            (30157.00, expected_contracts // 3),
            (30157.25, expected_contracts // 3),
            (30157.50, expected_contracts - 2 * (expected_contracts // 3)),
        ]
        for price, qty in fills:
            nt.simulate_entry_fill(trade_id, entry_price=price, fill_quantity=qty)

        deadline = time.time() + 2.0
        db_trade = None
        while time.time() < deadline:
            db_trade = app.trade_manager.trade_repository.get_trade(trade_id)
            if db_trade and db_trade.contracts == expected_contracts:
                break
            time.sleep(0.01)

        assert db_trade is not None
        assert db_trade.contracts == expected_contracts
        assert db_trade.entry_price == 30157.50

    def test_partial_entry_fills_preserve_original_sl_when_broker_omits_bracket(self, risk_harness: Any) -> None:
        """If the broker sends fills without SL/TP, Python must keep the originals."""
        app = risk_harness.app
        nt = risk_harness.nt

        with app.app.app_context():
            response = app.trades_controller.open_test_trade("MNQ", "long")
        assert response[1] == 201
        trade_id = response[0].json["trade_id"]

        cmd = nt.wait_for_command("order_open", timeout=5.0)
        payload = cmd["payload"]
        expected_contracts = max(
            1,
            int(round(payload["risk_usd"] / (payload["risk_points"] * 2.0))),
        )

        db_trade = app.trade_manager.trade_repository.get_trade(trade_id)
        original_sl = db_trade.stop_loss
        original_tp = db_trade.take_profit

        # Simulate partial fills where the broker does not report SL/TP.
        fills = [
            (30157.00, expected_contracts // 3),
            (30157.25, expected_contracts // 3),
            (30157.50, expected_contracts - 2 * (expected_contracts // 3)),
        ]
        for price, qty in fills:
            nt.simulate_entry_fill(trade_id, entry_price=price, fill_quantity=qty, include_bracket=False)

        deadline = time.time() + 2.0
        while time.time() < deadline:
            db_trade = app.trade_manager.trade_repository.get_trade(trade_id)
            if db_trade and db_trade.contracts == expected_contracts:
                break
            time.sleep(0.01)

        assert db_trade is not None
        assert db_trade.contracts == expected_contracts
        assert db_trade.stop_loss == original_sl
        assert db_trade.take_profit == original_tp
        assert db_trade.entry_price == 30157.50

    def test_mnq_point_value_default_is_two_dollars_per_point(self) -> None:
        """The MNQ full-point value must be $2.0, not the tick value $0.50."""
        from src.strategies.liquidity_v2.config import StrategyNumbers

        numbers = StrategyNumbers(
            min_stop_loss=10.0,
            max_bounce=90.0,
            extra_sl_space=0.0,
        )
        assert numbers.point_value == 2.0, (
            "MNQ point value default must be $2 per full point"
        )
