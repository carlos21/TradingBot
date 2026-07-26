"""End-to-end tests for the parity check feature.

Exercises ParityCheckService + NinjaTraderParityChecker + MarketClosureFilter
over real ZMQ sockets with FakeNinjaTrader.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from src.application.parity_service import ParityCheckService
from src.infrastructure.gateway.datasource import DataSourceState
from src.infrastructure.market_closure_filter import MarketClosureFilter
from src.infrastructure.parity_checker import NinjaTraderParityChecker
from tests.e2e.conftest import E2EHarness

# Fixed timestamp during CME ETH market hours (2024-06-10 14:00 UTC = 09:00 CDT).
# Using time.time() makes the tests time-dependent because gaps around Friday
# evening / weekend are classified as market-closed.
_MKT_HOURS_TS = int(datetime(2024, 6, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(t: int, open_: float, high: float, low: float, close: float, volume: int = 1) -> dict[str, Any]:
    return {
        "time": t,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "pair": "MNQ",
    }


def _feed_bar(nt, bar: dict[str, Any], delay_sec: float = 0.15) -> None:
    """Send a bar and give the consumer a moment to process (ZMQ slow-joiner protection)."""
    nt.send_bar(bar)
    time.sleep(delay_sec)


def _transition_to_live(nt, ds, delay_sec: float = 1.0) -> None:
    """Send empty history so the data source transitions to STREAMING state."""
    nt.send_history_batch([])
    nt.send_history_end(pair="MNQ")
    for _ in range(200):
        if ds.state == DataSourceState.STREAMING:
            break
        time.sleep(0.01)
    time.sleep(delay_sec)


def _create_parity_service(app) -> ParityCheckService:
    """Build a ParityCheckService wired to the harness app."""
    return ParityCheckService(
        data_source=app.data_source,
        gateway=app.data_source.gateway,
        checker=NinjaTraderParityChecker(market_filter=MarketClosureFilter(instrument="MNQ")),
        market_filter=MarketClosureFilter(instrument="MNQ"),
        logger=app.logger,
        pair="MNQ",
        instrument="MNQ 09-26",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParityCheckE2E:
    """Parity check end-to-end over real ZMQ."""

    def test_parity_all_good(self, e2e_harness: E2EHarness) -> None:
        """Stream identical bars to Python and FakeNT, run parity, assert all good."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = _MKT_HOURS_TS

        _transition_to_live(nt, app.data_source)

        bars = [
            _make_bar(now_ts - 120, 10.0, 11.0, 9.5, 10.5, 100),
            _make_bar(now_ts - 60, 10.5, 12.0, 10.0, 11.5, 200),
            _make_bar(now_ts, 11.5, 13.0, 11.0, 12.5, 300),
        ]
        for bar in bars:
            _feed_bar(nt, bar)

        service = _create_parity_service(app)
        result = service.check_parity(hours_back=5)

        assert result.all_good is True, f"Expected all_good but got: {result.summary}"
        assert result.gaps_found == 0

    def test_parity_detects_missing_bar(self, e2e_harness: E2EHarness) -> None:
        """Stream 5 bars, then manually send audit response missing one → gap detected."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = _MKT_HOURS_TS

        _transition_to_live(nt, app.data_source)

        bars = [
            _make_bar(now_ts - 240, 10.0, 11.0, 9.5, 10.5, 100),
            _make_bar(now_ts - 180, 10.5, 11.5, 10.0, 11.0, 200),
            _make_bar(now_ts - 120, 11.0, 12.0, 10.5, 11.5, 300),
            _make_bar(now_ts - 60, 11.5, 12.5, 11.0, 12.0, 400),
            _make_bar(now_ts, 12.0, 13.0, 11.5, 12.5, 500),
        ]
        for bar in bars:
            _feed_bar(nt, bar)

        # Manually send audit response missing bar 3
        nt.send_audit_response(bars[:2] + bars[3:])
        time.sleep(0.1)

        # Bypass the service's auto-request and compare directly
        python_bars = app.data_source.load_historical_bars("1m", pair="MNQ")
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter(instrument="MNQ"))
        result = checker.check(python_bars, bars[:2] + bars[3:])

        assert result.all_good is False
        assert result.gaps_found == 1
        assert result.gaps[0].gap_type == "extra"  # local has 5 bars, remote has 4
        assert result.gaps[0].start_time == bars[2]["time"]

    def test_parity_detects_mismatch(self, e2e_harness: E2EHarness) -> None:
        """Stream 3 bars, tamper close in audit response → mismatch detected."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = _MKT_HOURS_TS

        _transition_to_live(nt, app.data_source)

        bars = [
            _make_bar(now_ts - 120, 10.0, 11.0, 9.5, 10.5, 100),
            _make_bar(now_ts - 60, 10.5, 12.0, 10.0, 11.5, 200),
            _make_bar(now_ts, 11.5, 13.0, 11.0, 12.5, 300),
        ]
        for bar in bars:
            _feed_bar(nt, bar)

        # Tamper with bar 2's close
        tampered = [dict(b) for b in bars]
        tampered[1]["close"] = 99.9

        nt.send_audit_response(tampered)
        time.sleep(0.1)

        python_bars = app.data_source.load_historical_bars("1m", pair="MNQ")
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter(instrument="MNQ"))
        result = checker.check(python_bars, tampered)

        assert result.all_good is False
        assert result.gaps_found == 1
        assert result.gaps[0].gap_type == "mismatch"
        assert result.gaps[0].start_time == bars[1]["time"]

    def test_parity_filters_market_closed_gap(self, e2e_harness: E2EHarness) -> None:
        """Stream bars with a CME maintenance gap → all_good=True with 🌙 gap."""
        from datetime import datetime, timezone

        nt = e2e_harness.nt
        app = e2e_harness.app

        _transition_to_live(nt, app.data_source)

        # CME maintenance window: 16:00-17:01 CDT on 2024-06-10
        # Use a bar well inside the window: 16:30 CDT = 21:30 UTC
        maint_mid = int(datetime(2024, 6, 10, 21, 30, 0, tzinfo=timezone.utc).timestamp())
        before_maint = maint_mid - 60

        bars_local = [
            _make_bar(before_maint, 10.0, 11.0, 9.5, 10.5, 100),
        ]
        bars_remote = [
            _make_bar(before_maint, 10.0, 11.0, 9.5, 10.5, 100),
            _make_bar(maint_mid, 11.0, 12.0, 10.5, 11.5, 300),  # bar during maintenance
        ]

        for bar in bars_local:
            _feed_bar(nt, bar)

        python_bars = app.data_source.load_historical_bars("1m", pair="MNQ")
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter(instrument="MNQ"))
        result = checker.check(python_bars, bars_remote)

        assert result.all_good is True, f"Expected all_good=True for market-closed gap but got: {result.summary}"
        assert len(result.gaps) == 1
        assert result.gaps[0].is_market_closed is True
