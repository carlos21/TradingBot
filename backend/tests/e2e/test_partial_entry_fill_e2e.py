"""End-to-end test for a partial-then-full entry fill lifecycle.

Mirrors the 2026-07-29 live incident: a 3-contract MNQ entry filled partially
(1/3) and then fully (3/3). NinjaTrader sent two ENTRY_FILL messages — first
qty=1 with the bracket SL/TP, then cumulative qty=3 with recalculated SL/TP
(the average fill price moved). Python must treat this as ONE trade that
updates in place to the cumulative quantity and the latest SL/TP, and close
cleanly on the SL exit with correct 3-contract P&L.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.e2e.conftest import E2EHarness
from tests.fakes import FakeLogger

# Incident values (MNQ long, 3 contracts).
QTY = 3
RISK_USD = 500.0  # ZMQTradeExecutor default in the harness
ORDER_ENTRY = 27551.25
ORDER_RISK_POINTS = 80.0  # round(500 / (80 * $2)) = 3 contracts at the broker
ORDER_SL = ORDER_ENTRY - ORDER_RISK_POINTS  # 27471.25
ORDER_TP = ORDER_ENTRY + 264.0  # 27815.25 (rr 3.3)

FIRST_FILL_PRICE = 27551.25
FIRST_SL = 27521.25
FIRST_TP = 27701.25

# Second fill: average entry moved, bracket recalculated around the new average.
FINAL_ENTRY = 27551.4167
FINAL_SL = 27521.4167
FINAL_TP = 27701.4167

POINT_VALUE = 2.0  # MNQ $2/point
FEE_PER_RT = 1.50  # FinancialCalc.DEFAULT_FEE_PER_RT
EXPECTED_FEES = QTY * FEE_PER_RT  # 4.50
EXPECTED_RESULT_R = -1.0  # stopped out exactly at the 30-point stop
EXPECTED_PNL_USD = QTY * EXPECTED_RESULT_R * 30.0 * POINT_VALUE - EXPECTED_FEES  # -184.50


class _CapturingLogger(FakeLogger):
    """FakeLogger that records warnings/errors for assertions."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


@pytest.fixture
def e2e_logger() -> _CapturingLogger:
    """Override the shared no-op logger so this module can inspect log output."""
    return _CapturingLogger()


def _wait_for_trade_record(
    trade_repo, trade_id: str, predicate, timeout: float = 5.0
) -> Any:
    """Poll the repository until the trade record satisfies ``predicate``."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        trade = trade_repo.get_trade(trade_id)
        if trade is not None and predicate(trade):
            return trade
        time.sleep(0.01)
    raise TimeoutError(f"Trade {trade_id} record did not reach expected state")


def _assert_no_platform_errors(logger: _CapturingLogger) -> None:
    """No PLATFORM ERROR / orphan / missing-stop-loss handling may trigger."""
    assert logger.errors == [], f"unexpected error logs: {logger.errors}"
    bad_warnings = [
        w for w in logger.warnings
        if "ORPHAN" in w or "not in memory" in w or "untracked" in w
    ]
    assert bad_warnings == [], f"unexpected orphan/missing-fill warnings: {bad_warnings}"


class TestPartialEntryFillE2E:
    def test_partial_then_full_entry_fill_single_trade(
        self, e2e_harness: E2EHarness, e2e_logger: _CapturingLogger
    ) -> None:
        app = e2e_harness.app
        nt = e2e_harness.nt

        # 1) Open a 3-contract long through the real wiring.
        trade = app.trade_manager.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=ORDER_ENTRY,
            stop_loss=ORDER_SL,
            take_profit=ORDER_TP,
            risk=ORDER_RISK_POINTS,
            entry_time=time.time(),
            rr_ratio=3.3,
        )
        trade_id = trade["trade_id"]

        open_cmd = nt.wait_for_command("order_open")
        assert open_cmd["payload"]["trade_id"] == trade_id
        # The fake broker sizes from risk_usd like real NT: 3 contracts.
        assert nt._tracker.get_entry(trade_id).quantity == QTY

        # 2) First partial fill: 1/3 with the bracket SL/TP.
        nt.simulate_entry_fill(
            trade_id,
            entry_price=FIRST_FILL_PRICE,
            stop_loss=FIRST_SL,
            take_profit=FIRST_TP,
            fill_quantity=1,
        )
        db_trade = _wait_for_trade_record(
            app.trade_manager.trade_repository,
            trade_id,
            lambda t: t.contracts == 1,
        )
        assert db_trade.entry_price == pytest.approx(FIRST_FILL_PRICE)
        assert db_trade.stop_loss == pytest.approx(FIRST_SL)
        assert db_trade.take_profit == pytest.approx(FIRST_TP)
        assert len(app.trade_manager.open_trades) == 1
        assert len(app.trade_manager.trade_repository.list_trades("MNQ")) == 1
        _assert_no_platform_errors(e2e_logger)

        # 3) Second fill completes the order: cumulative qty=3, new average
        #    entry price and recalculated SL/TP (the incident sequence).
        nt.simulate_entry_fill(
            trade_id,
            entry_price=FINAL_ENTRY,
            stop_loss=FINAL_SL,
            take_profit=FINAL_TP,
            fill_quantity=2,
        )
        db_trade = _wait_for_trade_record(
            app.trade_manager.trade_repository,
            trade_id,
            lambda t: t.contracts == QTY,
        )
        # Still the SAME single trade, updated in place — no duplicate.
        assert len(app.trade_manager.open_trades) == 1
        assert app.trade_manager.open_trades[0]["trade_id"] == trade_id
        assert len(app.trade_manager.trade_repository.list_trades("MNQ")) == 1
        assert db_trade.entry_price == pytest.approx(FINAL_ENTRY)
        assert db_trade.stop_loss == pytest.approx(FINAL_SL)
        assert db_trade.take_profit == pytest.approx(FINAL_TP)
        _assert_no_platform_errors(e2e_logger)

        # 4) SL exit fill for the full position.
        nt.simulate_exit_fill(trade_id, exit_price=FINAL_SL, result_type="SL")
        closed = _wait_for_trade_record(
            app.trade_manager.trade_repository,
            trade_id,
            lambda t: t.exit_time is not None,
        )
        assert closed.result_type == "SL"
        assert closed.exit_price == pytest.approx(FINAL_SL)
        # 3 contracts, 30-point stop, $2/point: -180 gross, -4.50 fees.
        assert closed.result == pytest.approx(EXPECTED_RESULT_R)
        assert closed.fees == pytest.approx(EXPECTED_FEES)
        assert closed.pnl_usd == pytest.approx(EXPECTED_PNL_USD)
        # Trade removed from memory; still exactly one (closed) record.
        assert app.trade_manager.open_trades == []
        assert len(app.trade_manager.trade_repository.list_trades("MNQ")) == 1
        _assert_no_platform_errors(e2e_logger)
