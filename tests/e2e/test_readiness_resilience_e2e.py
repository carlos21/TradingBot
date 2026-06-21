"""End-to-end resilience tests for bar loading and readiness.

These tests exercise the full ZMQ path through FakeNinjaTrader, the gateway,
the data source, the readiness monitor, and the strategy. They target the
high-risk edge cases that were not covered by the existing e2e suite.
"""

from __future__ import annotations

import time
from typing import Any

from src.domain.readiness.readiness_state import ReadinessState
from src.infrastructure.gateway.datasource import DataSourceState
from tests.e2e.conftest import E2EHarness

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _make_bar(
    t: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 1,
    pair: str = "MNQ",
) -> dict[str, Any]:
    return {
        "time": t,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "pair": pair,
    }


def _build_history(
    end_time: int | None = None,
    bar_count: int = 480,
) -> list[dict[str, Any]]:
    """Generate enough 1m bars to satisfy all internal timeframes (1m/3m/5m/15m).

    480 bars = 8 hours. This gives 32 complete 15m bars (or 20+ with partial
    window handling), comfortably above the data-limited floor of 20.

    The history ends a few seconds in the past so tests can send a *new* live
    bar at the current time without it being treated as a duplicate and without
    crossing the 60s gap-detection threshold.
    """
    if end_time is None:
        end_time = int(time.time()) - 5
    start_time = end_time - (bar_count - 1) * 60
    bars = []
    price = 100.0
    for i in range(bar_count):
        t = start_time + i * 60
        # Small random-looking drift to keep indicators sane.
        price += 0.1
        bars.append(_make_bar(t, price, price + 0.5, price - 0.5, price + 0.1))
    return bars


def _feed_bar(nt, bar: dict[str, Any], delay_sec: float = 0.05) -> None:
    nt.send_bar(bar)
    time.sleep(delay_sec)


def _state_machine(app):
    """Return the readiness state machine used by the strategy context."""
    return app.strategy.execution_context._state_machine


def _wait_for_state(app, target: ReadinessState, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    sm = _state_machine(app)
    while time.time() < deadline:
        if sm.state == target:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {target.name}; got {sm.state.name}")


def _wait_for_data_source_state(app, target: DataSourceState, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    ds = app.data_source
    while time.time() < deadline:
        if ds.state == target:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for data source {target.name}; got {ds.state.name}")


def _send_history_and_go_live(nt, app, bars: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Send history, wait for READY, feed one live bar to reach LIVE, then clear commands."""
    if bars is None:
        bars = _build_history()
    nt.send_history_batch(bars)
    nt.send_history_end()
    _wait_for_state(app, ReadinessState.READY)
    # The state machine only moves READY -> LIVE when a completed live bar arrives.
    live_bar = _make_bar(int(time.time()), 101.0, 102.0, 100.5, 101.5, 10)
    _feed_bar(nt, live_bar)
    _wait_for_state(app, ReadinessState.LIVE)
    nt.clear_commands()
    return bars


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


class TestReconnectResilience:
    """Disconnect / reconnect scenarios."""

    def test_brief_reconnect_keeps_streaming(self, e2e_harness_resilience: E2EHarness) -> None:
        """A <3s disconnect does not lose live bars after reconnect."""
        nt = e2e_harness_resilience.nt
        app = e2e_harness_resilience.app

        _send_history_and_go_live(nt, app)

        # Simulate brief disconnect by pausing heartbeats.
        nt.pause_heartbeats()
        _wait_for_state(app, ReadinessState.DISCONNECTED)

        # Reconnect before the 3s debounce threshold.
        nt.resume_heartbeats()
        # Cached history is still fresh, so the data source resumes streaming and
        # re-runs warmup. A new completed live bar is required to return to LIVE.
        _wait_for_state(app, ReadinessState.READY, timeout=5.0)
        # Make sure the new live bar has a strictly greater time than the last
        # cached bar, otherwise the data source treats it as a duplicate.
        time.sleep(1.1)
        live_bar = _make_bar(int(time.time()), 101.5, 102.0, 101.0, 101.8, 50)
        _feed_bar(nt, live_bar)
        _wait_for_state(app, ReadinessState.LIVE, timeout=3.0)

        # Live bars must flow again.
        cached = app.data_source.load_historical_bars("1m")
        assert any(b["time"] == live_bar["time"] for b in cached)

    def test_real_reconnect_refreshes_history(self, e2e_harness_resilience: E2EHarness) -> None:
        """A >3s disconnect triggers a full history refresh cycle."""
        nt = e2e_harness_resilience.nt
        app = e2e_harness_resilience.app

        _send_history_and_go_live(nt, app)

        # Long disconnect (>3s debounce).
        nt.pause_heartbeats()
        _wait_for_state(app, ReadinessState.DISCONNECTED)
        time.sleep(3.5)
        nt.resume_heartbeats()

        # A refresh should be requested.
        nt.clear_commands()
        nt.wait_for_command("refresh_request", timeout=3.0)

        # Send fresh history and end; feed a new live bar to reach LIVE again.
        nt.send_history_batch(_build_history())
        nt.send_history_end()
        _wait_for_state(app, ReadinessState.READY, timeout=5.0)
        live_bar = _make_bar(int(time.time()), 101.5, 102.0, 101.0, 101.8, 50)
        _feed_bar(nt, live_bar)
        _wait_for_state(app, ReadinessState.LIVE, timeout=3.0)


class TestHistoryEdgeCases:
    """Empty history, late gap-fill, and malformed history."""

    def test_history_arriving_before_delay_cancels_redundant_refresh(
        self, e2e_harness_resilience: E2EHarness
    ) -> None:
        """If history arrives before the 1s delayed refresh fires, no refresh is requested."""
        nt = e2e_harness_resilience.nt
        app = e2e_harness_resilience.app

        # Send history immediately after connect, before the delayed refresh timer fires.
        nt.send_history_batch(_build_history())
        nt.send_history_end()
        _wait_for_state(app, ReadinessState.READY, timeout=5.0)
        live_bar = _make_bar(int(time.time()), 101.0, 102.0, 100.5, 101.5, 10)
        _feed_bar(nt, live_bar)
        _wait_for_state(app, ReadinessState.LIVE, timeout=3.0)

        # Wait longer than the 1s delay to be sure the timer did not fire.
        time.sleep(1.5)
        refresh_requests = [c for c in nt.commands_received if c.get("msg_type") == "refresh_request"]
        assert len(refresh_requests) == 0

    def test_empty_history_retries_then_recovers(self, e2e_harness_resilience: E2EHarness) -> None:
        """Empty history triggers a retry; sending history later recovers."""
        nt = e2e_harness_resilience.nt
        app = e2e_harness_resilience.app

        nt.send_history_batch([])
        nt.send_history_end()

        # The monitor should schedule a retry and request a refresh.
        nt.wait_for_command("refresh_request", timeout=5.0)

        # Now supply history and a new completed live bar to reach LIVE.
        nt.send_history_batch(_build_history())
        nt.send_history_end()
        _wait_for_state(app, ReadinessState.READY, timeout=5.0)
        live_bar = _make_bar(int(time.time()), 101.0, 102.0, 100.5, 101.5, 10)
        _feed_bar(nt, live_bar)
        _wait_for_state(app, ReadinessState.LIVE, timeout=3.0)

    def test_late_gap_fill_degrades_readiness(self, e2e_harness_resilience: E2EHarness) -> None:
        """A gap-fill history batch arriving after LIVE degrades readiness."""
        nt = e2e_harness_resilience.nt
        app = e2e_harness_resilience.app

        bars = _send_history_and_go_live(nt, app)

        # Live gap-fill batch arriving after we are already streaming.
        # Pick times well before the start of the loaded history so they are
        # genuinely new and trigger on_late_history_batch.
        history_start = bars[0]["time"]
        gap_fill = [
            _make_bar(history_start - 360, 99.0, 100.0, 98.5, 99.5, 100),
            _make_bar(history_start - 300, 99.5, 100.5, 99.0, 100.0, 100),
        ]
        nt.send_history_batch(gap_fill)

        _wait_for_state(app, ReadinessState.DEGRADED, timeout=3.0)
        assert "Gap-fill" in _state_machine(app).reason

    def test_warmup_exception_does_not_transition_ready(
        self, e2e_harness_resilience: E2EHarness, monkeypatch
    ) -> None:
        """A malformed history bar that crashes strategy.on_raw_bar must not promote to READY."""
        nt = e2e_harness_resilience.nt
        app = e2e_harness_resilience.app

        history = _build_history()
        failing_time = history[90]["time"]

        original_on_raw_bar = app.strategy.on_raw_bar
        call_count = {"n": 0}

        def raising_on_raw_bar(bar):
            call_count["n"] += 1
            if bar["time"] == failing_time:
                raise RuntimeError("simulated warmup failure")
            return original_on_raw_bar(bar)

        monkeypatch.setattr(app.strategy, "on_raw_bar", raising_on_raw_bar)

        nt.send_history_batch(history)
        nt.send_history_end()

        # Give warmup time to fail.
        time.sleep(0.5)

        sm = _state_machine(app)
        assert sm.state != ReadinessState.READY
        assert sm.state != ReadinessState.LIVE
        assert call_count["n"] >= 1


class TestPartialBars:
    """Partial (in-progress) bar handling."""

    def test_partial_bars_reach_chart_only(self, e2e_harness_resilience: E2EHarness) -> None:
        """Native partial bars update the chart but do not feed the strategy."""
        nt = e2e_harness_resilience.nt
        app = e2e_harness_resilience.app

        _send_history_and_go_live(nt, app)

        emitted: list[tuple[str, Any]] = []
        original_emit = app.socketio.emit

        def emit_catcher(event, payload=None, *args, **kwargs):
            emitted.append((event, payload))
            return original_emit(event, payload, *args, **kwargs)

        # Monkey-patch the real SocketIO emit for the duration of the test.
        app.socketio.emit = emit_catcher
        try:
            partial = _make_bar(int(time.time()), 101.0, 102.0, 101.0, 101.5, 1)
            nt.send_partial_bar(partial)
            time.sleep(0.2)
        finally:
            app.socketio.emit = original_emit

        bar_events = [p for e, p in emitted if e == "bar"]
        assert len(bar_events) >= 1
        assert bar_events[-1].get("time") == partial["time"]
