"""End-to-end test for the full readiness flow without shortcuts.

Drives the real ZMQDataSource + ReadinessMonitor + strategy through
connect -> refresh -> history -> live bar and asserts that:
  - live bars are blocked/dropped until readiness reaches READY/LIVE
  - readiness state transitions correctly
  - history_loaded and trading_ready Socket.IO events are emitted
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest

from src.domain.readiness import ReadinessState
from src.infrastructure.gateway.datasource import ZMQDataSource


# Reduce the number of bars needed by warming a 1m timeframe.
# The live_app fixture uses 5m by default.
def _make_history_bars(count: int, pair: str = "MNQ") -> list[dict[str, Any]]:
    now = int(time.time()) - 3600  # 1 hour ago so gaps are not market-closed
    return [
        {
            "time": now - (count - i) * 60,
            "open": 21000.0 + i,
            "high": 21010.0 + i,
            "low": 20990.0 + i,
            "close": 21000.0 + i,
            "volume": 100 + i,
            "pair": pair,
        }
        for i in range(count)
    ]


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
class _ReadinessHarness:
    nt: Any
    app: Any
    catcher: _EmitCatcher


def _wait_for_state(app, target_name: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    monitor = getattr(app.data_source, "_readiness_monitor", None)
    while time.time() < deadline:
        if monitor is not None and monitor._state_machine.state.name == target_name:
            return
        time.sleep(0.01)
    actual = monitor._state_machine.state.name if monitor else "NO_MONITOR"
    raise TimeoutError(f"Readiness state never reached {target_name}; actual={actual}")


@pytest.fixture
def readiness_harness(fake_nt, live_app):
    """E2E harness that does NOT force the state machine to LIVE."""
    app = live_app
    nt = fake_nt

    # Patch strategy to only need 1m warmup; live_app uses 5m by default.
    app.strategy.timeframes = ["1m"]

    # Track every bar the strategy actually processes.
    original_on_raw_bar = app.strategy.on_raw_bar
    app.strategy.processed_bars: list[dict[str, Any]] = []

    def _tracking_on_raw_bar(bar):
        app.strategy.processed_bars.append(bar)
        return original_on_raw_bar(bar)

    app.strategy.on_raw_bar = _tracking_on_raw_bar

    # Bypass the wall-clock freshness check and indicator-warmup gate so the
    # test exercises the readiness flow without needing huge bar histories.
    if isinstance(app.data_source, ZMQDataSource):
        app.data_source.check_history_completeness = lambda bars=None: (True, "test")

    monitor = getattr(app.data_source, "_readiness_monitor", None)
    if monitor is not None:
        class _AlwaysWarm:
            def is_warm(self, _strategy):
                return True
        monitor._warmup_policy = _AlwaysWarm()

    catcher = _EmitCatcher(app.socketio)

    time.sleep(0.3)  # ZMQ slow-joiner protection
    nt.send_connect(pair="MNQ")
    time.sleep(0.2)  # let platform_connected propagate

    yield _ReadinessHarness(nt=nt, app=app, catcher=catcher)

    catcher.restore()
    app.strategy.on_raw_bar = original_on_raw_bar


class TestReadinessFlowE2E:
    def test_history_then_live_bar_flow(self, readiness_harness: _ReadinessHarness) -> None:
        nt = readiness_harness.nt
        app = readiness_harness.app
        monitor = app.data_source._readiness_monitor
        catcher = readiness_harness.catcher

        # 1) Platform connects and schedules refresh automatically.
        assert monitor._state_machine.state == ReadinessState.CONNECTED

        # 2) NinjaTrader sends history.
        bars = _make_history_bars(100)  # enough bars for 1m warmup/indicators
        nt.send_history_batch(bars)
        nt.send_history_end()

        # 3) Wait for READY.
        _wait_for_state(app, "READY")

        # 4) Assert history_loaded and trading_ready were emitted.
        emitted_events = [e for e, _ in catcher.events]
        assert "history_loaded" in emitted_events
        assert "trading_ready" in emitted_events

        # 5) Send a live bar while still READY. Keep it contiguous with the
        # historical batch so the gap detector does not degrade the state.
        live_bar = {
            "time": bars[-1]["time"] + 60,
            "open": 22000.0,
            "high": 22010.0,
            "low": 21990.0,
            "close": 22000.0,
            "volume": 1000,
            "pair": "MNQ",
        }
        nt.send_bar(live_bar)

        # 6) Wait for LIVE and verify the bar was processed.
        _wait_for_state(app, "LIVE")
        deadline = time.time() + 2.0
        while time.time() < deadline and not any(b.get("time") == live_bar["time"] for b in app.strategy.processed_bars):
            time.sleep(0.01)

        processed = [b for b in app.strategy.processed_bars if b.get("time") == live_bar["time"]]
        assert len(processed) == 1

    def test_live_bar_dropped_before_ready(self, readiness_harness: _ReadinessHarness) -> None:
        nt = readiness_harness.nt
        app = readiness_harness.app
        monitor = app.data_source._readiness_monitor

        # Send a live bar before any history has arrived.
        early_bar = {
            "time": int(time.time()) - 60,
            "open": 21000.0,
            "high": 21010.0,
            "low": 20990.0,
            "close": 21000.0,
            "volume": 100,
            "pair": "MNQ",
        }
        nt.send_bar(early_bar)
        time.sleep(0.2)

        # Strategy should not have processed the bar while CONNECTED.
        assert monitor._state_machine.state == ReadinessState.CONNECTED
        historical = [b for b in app.data_source._historical_bars if b["time"] == early_bar["time"]]
        assert historical == []
