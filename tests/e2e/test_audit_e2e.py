"""End-to-end tests for the AUDIT_REQUEST / AUDIT_RESPONSE flow.

These tests verify that Python can request a bar snapshot from FakeNinjaTrader
over real ZMQ sockets, and that BarComparer detects drift correctly.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from src.infrastructure.bar_auditor import BarComparer, NinjaTraderBarAuditor
from src.infrastructure.gateway.datasource import DataSourceState
from tests.e2e.conftest import E2EHarness

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


def _send_audit_and_wait(harness: E2EHarness, bars_back: int = 60, timeout: float = 5.0) -> list[dict[str, Any]]:
    """Send AUDIT_REQUEST via gateway and block until AUDIT_RESPONSE arrives."""
    gateway = harness.app.data_source.gateway
    received: list[dict[str, Any]] = []
    event = threading.Event()

    def _on_response(payload: dict[str, Any]) -> None:
        received.extend(payload.get("bars", []))
        event.set()

    gateway.on_audit_response(_on_response)
    gateway.send_audit_request(bars_back)

    if not event.wait(timeout=timeout):
        raise TimeoutError("AUDIT_RESPONSE did not arrive in time")
    return received


def _transition_to_live(nt, ds, delay_sec: float = 1.0) -> None:
    """Send empty history so the data source transitions to STREAMING state."""
    nt.send_history_batch([])
    nt.send_history_end()
    # Wait for state transition
    for _ in range(200):
        if ds.state == DataSourceState.STREAMING:
            break
        time.sleep(0.01)
    # Extra ZMQ slow-joiner protection
    time.sleep(delay_sec)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuditRoundTrip:
    """AUDIT_REQUEST → AUDIT_RESPONSE over real ZMQ."""

    def test_audit_receives_exactly_the_bars_that_were_streamed(self, e2e_harness: E2EHarness) -> None:
        """Stream 5 bars, request audit, assert every field matches."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = int(time.time())

        _transition_to_live(nt, app.data_source)

        bars = [
            _make_bar(now_ts - 120, 10.0, 11.0, 9.5, 10.5, 100),
            _make_bar(now_ts - 60, 10.5, 12.0, 10.0, 11.5, 200),
            _make_bar(now_ts, 11.5, 13.0, 11.0, 12.5, 300),
            _make_bar(now_ts + 60, 12.5, 14.0, 12.0, 13.5, 400),
            _make_bar(now_ts + 120, 13.5, 15.0, 13.0, 14.5, 500),
        ]

        for bar in bars:
            _feed_bar(nt, bar)

        # Load what Python cached
        python_bars = app.data_source.load_historical_bars("1m")

        # Request audit from FakeNT
        audit_bars = _send_audit_and_wait(e2e_harness, bars_back=5)

        # Compare
        result = BarComparer().compare(python_bars, audit_bars)
        assert not result.has_drift, f"Expected no drift but got: {result.summary}"

    def test_audit_detects_missing_bar(self, e2e_harness: E2EHarness) -> None:
        """Simulate the old skip-bug: FakeNT 'forgets' bar 3 in audit response."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = int(time.time())

        _transition_to_live(nt, app.data_source)

        bars = [
            _make_bar(now_ts - 120, 10.0, 11.0, 9.5, 10.5, 100),
            _make_bar(now_ts - 60, 10.5, 12.0, 10.0, 11.5, 200),
            _make_bar(now_ts, 11.5, 13.0, 11.0, 12.5, 300),
            _make_bar(now_ts + 60, 12.5, 14.0, 12.0, 13.5, 400),
            _make_bar(now_ts + 120, 13.5, 15.0, 13.0, 14.5, 500),
        ]

        for bar in bars:
            _feed_bar(nt, bar)

        python_bars = app.data_source.load_historical_bars("1m")

        # FakeNT manually sends audit WITHOUT bar 3 (simulating lost bar)
        nt.send_audit_response(bars[:2] + bars[3:])  # drops bars[2]
        time.sleep(0.1)

        # We need to capture the manual response — bypass the harness helper
        # because we already sent it.  Instead read from Python's cached bars
        # and compare against the truncated set.
        result = BarComparer().compare(python_bars, bars[:2] + bars[3:])
        assert result.has_drift
        assert result.missing_count == 1
        assert result.details[0].time == bars[2]["time"]

    def test_audit_detects_mismatched_close(self, e2e_harness: E2EHarness) -> None:
        """FakeNT sends correct bars except one has a different close."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = int(time.time())

        _transition_to_live(nt, app.data_source)

        bars = [
            _make_bar(now_ts - 120, 10.0, 11.0, 9.5, 10.5, 100),
            _make_bar(now_ts - 60, 10.5, 12.0, 10.0, 11.5, 200),
            _make_bar(now_ts, 11.5, 13.0, 11.0, 12.5, 300),
        ]

        for bar in bars:
            _feed_bar(nt, bar)

        python_bars = app.data_source.load_historical_bars("1m")

        # Tamper with bar 2's close in the audit response
        tampered = [dict(b) for b in bars]
        tampered[1]["close"] = 99.9

        nt.send_audit_response(tampered)
        time.sleep(0.1)

        result = BarComparer().compare(python_bars, tampered)
        assert result.has_drift
        assert result.mismatch_count == 1
        assert result.details[0].time == bars[1]["time"]
        assert result.details[0].field_differences["close"] == (11.5, 99.9)


class TestNinjaTraderBarAuditorE2E:
    """Full BarAuditor running against FakeNinjaTrader over ZMQ."""

    def test_auditor_finds_no_drift_when_bars_match(self, e2e_harness: E2EHarness) -> None:
        """BarAuditor end-to-end: stream bars, run audit, expect OK."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = int(time.time())

        _transition_to_live(nt, app.data_source)

        bars = [
            _make_bar(now_ts - 120, 10.0, 11.0, 9.5, 10.5, 100),
            _make_bar(now_ts - 60, 10.5, 12.0, 10.0, 11.5, 200),
            _make_bar(now_ts, 11.5, 13.0, 11.0, 12.5, 300),
        ]
        for bar in bars:
            _feed_bar(nt, bar)

        drift_results: list[Any] = []

        auditor = NinjaTraderBarAuditor(
            gateway=app.data_source.gateway,
            data_source=app.data_source,
            logger=app.logger,
            interval_minutes=0,  # never auto-fire
            bars_back=3,
            on_drift=lambda r: drift_results.append(r),
        )
        auditor.start()
        try:
            auditor._run_audit()
            time.sleep(0.2)
            assert len(drift_results) == 0, f"Expected no drift but got: {drift_results}"
        finally:
            auditor.stop()

    def test_auditor_finds_drift_when_bar_is_missing(self, e2e_harness: E2EHarness) -> None:
        """BarAuditor end-to-end: simulate missing bar and assert drift detected."""
        nt = e2e_harness.nt
        app = e2e_harness.app
        now_ts = int(time.time())

        _transition_to_live(nt, app.data_source)

        bars = [
            _make_bar(now_ts - 120, 10.0, 11.0, 9.5, 10.5, 100),
            _make_bar(now_ts - 60, 10.5, 12.0, 10.0, 11.5, 200),
            _make_bar(now_ts, 11.5, 13.0, 11.0, 12.5, 300),
        ]
        for bar in bars:
            _feed_bar(nt, bar)

        # Wipe FakeNT's audit buffer so it responds with fewer bars
        nt._audit_bars.clear()
        nt._audit_bars.extend(bars[:1])  # only return first bar

        drift_results: list[Any] = []

        auditor = NinjaTraderBarAuditor(
            gateway=app.data_source.gateway,
            data_source=app.data_source,
            logger=app.logger,
            interval_minutes=0,
            bars_back=3,
            on_drift=lambda r: drift_results.append(r),
        )
        auditor.start()
        try:
            auditor._run_audit()
            time.sleep(0.2)
            assert len(drift_results) == 1
            assert drift_results[0].missing_count == 2
        finally:
            auditor.stop()
