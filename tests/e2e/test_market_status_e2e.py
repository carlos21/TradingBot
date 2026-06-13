"""End-to-end tests for the MARKET_STATUS message flow.

Exercises FakeNinjaTrader → ZMQ → TradingGateway → ZMQDataSource.
"""

from __future__ import annotations

import time

import pytest

from src.infrastructure.gateway.datasource import DataSourceState
from tests.e2e.conftest import E2EHarness


def _feed_bar(nt, bar: dict) -> None:
    nt.send_bar(bar)
    time.sleep(0.02)


class TestMarketStatusFlow:
    """MARKET_STATUS → duplicate suppression → stale fallback."""

    def test_market_status_received_by_datasource(self, e2e_harness: E2EHarness) -> None:
        nt = e2e_harness.nt
        ds = e2e_harness.app.data_source

        nt.send_market_status(market_open=True, next_open=1700000000, pair="MNQ")
        time.sleep(0.05)

        assert ds._market_is_open is True

    def test_duplicate_bar_suppressed_when_market_closed(self, e2e_harness: E2EHarness) -> None:
        nt = e2e_harness.nt
        ds = e2e_harness.app.data_source
        ds._state = DataSourceState.STREAMING

        # Set market closed first, then feed duplicate bars
        nt.send_market_status(market_open=False, next_open=1700004600, pair="MNQ")
        time.sleep(0.05)
        assert ds._market_is_open is False

        bar = {"time": 1700001000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100, "pair": "MNQ"}
        _feed_bar(nt, bar)
        # Wait for first bar to be processed before sending duplicate
        time.sleep(0.05)
        _feed_bar(nt, bar)
        time.sleep(0.05)

        assert ds._duplicate_count == 1
        # Bar arrival re-opened the market (expected recovery behaviour)
        assert ds._market_is_open is True

    def test_bar_arrival_reopens_market(self, e2e_harness: E2EHarness) -> None:
        nt = e2e_harness.nt
        ds = e2e_harness.app.data_source
        ds._state = DataSourceState.STREAMING

        nt.send_market_status(market_open=False, next_open=1700004600, pair="MNQ")
        time.sleep(0.05)
        assert ds._market_is_open is False

        bar = {"time": 1700002000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100, "pair": "MNQ"}
        _feed_bar(nt, bar)

        assert ds._market_is_open is True

    def test_stale_fallback_closes_market(self, e2e_harness: E2EHarness) -> None:
        ds = e2e_harness.app.data_source

        ds._market_is_open = True
        ds._state = DataSourceState.STREAMING
        ds._heartbeat_check_interval_sec = 0.01
        ds._start_heartbeat_monitor()
        ds._last_completed_bar_time = time.monotonic() - 400
        time.sleep(0.05)
        ds._stop_heartbeat_monitor()

        assert ds._market_is_open is False
