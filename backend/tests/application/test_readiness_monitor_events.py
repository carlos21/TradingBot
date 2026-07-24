"""Tests for ReadinessMonitor Socket.IO event emission.

This covers the market-closed scenario: historical bars are loaded but are too
stale to transition to READY, so the chart must still be notified via
``history_loaded`` even though ``trading_ready`` is not emitted.
"""

from __future__ import annotations

from typing import Any

from src.application.live_readiness.live_bar_buffer import LiveBarBuffer
from src.application.live_readiness.readiness_monitor import ReadinessMonitor
from src.domain.readiness import ReadinessStateMachine
from src.strategies.liquidity_v2.base_strategy import LineRemovalMode
from tests.fakes import DummySocketIO, FakeLogger


class _FakeStrategy:
    """Minimal stand-in for LiquidityStrategyV2."""

    def __init__(self) -> None:
        self.logger = FakeLogger()
        self.internal_timeframes = ["1m"]
        self._history: list[dict[str, Any]] = []
        self.warmup_crossed_lines: set[str] = set()
        self.strategy_lines: dict[str, Any] = {}
        self.options = _FakeOptions()

    def on_raw_bar(self, bar: dict[str, Any]) -> None:
        self._history.append(bar)

    def get_history(self, tf: str, count: int) -> list[dict[str, Any]]:
        return self._history[-count:]

    def restore_trigger_states(self, pair: str) -> None:
        pass

    def restore_open_trades(self) -> None:
        pass

    def restore_reentry_opportunities(self, pair: str) -> None:
        pass


class _FakeOptions:
    line_removal_mode = LineRemovalMode.NEVER


class _FakeWarmupOrchestrator:
    """Records that it ran and feeds bars to the fake strategy."""

    def __init__(self, strategy: _FakeStrategy) -> None:
        self._strategy = strategy
        self.ran = False

    @property
    def strategy(self) -> _FakeStrategy:
        return self._strategy

    def cancel(self) -> None:
        pass

    def reset_cancel(self) -> None:
        pass

    def set_progress_listener(self, listener: Any) -> None:
        pass

    def run(self, bars: list[dict[str, Any]], pair: str) -> None:
        self.ran = True
        for bar in bars:
            self._strategy.on_raw_bar(bar)


class _FakeWarmupPolicy:
    def __init__(self, warm: bool = True) -> None:
        self._warm = warm

    def is_warm(self, strategy: Any) -> bool:
        return self._warm


class _StaleDataSource:
    """Simulates history whose newest bar is too old for trading."""

    def check_history_completeness(
        self, bars: list[dict[str, Any]] | None = None
    ) -> tuple[bool, str]:
        return False, "Last bar is 120m old (need < 1m)"


class _FreshDataSource:
    """Simulates history whose newest bar is recent enough for trading."""

    def check_history_completeness(
        self, bars: list[dict[str, Any]] | None = None
    ) -> tuple[bool, str]:
        return True, "OK"


def _make_bars(count: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "time": 1000 + i * 60,
            "open": 1.0,
            "high": 2.0,
            "low": 0.0,
            "close": 1.0,
            "volume": 1,
            "pair": "MNQ",
        }
        for i in range(count)
    ]


def _make_monitor(
    socketio: DummySocketIO,
    ds: Any,
    warm_policy: _FakeWarmupPolicy,
) -> tuple[ReadinessMonitor, _FakeStrategy]:
    strategy = _FakeStrategy()
    sm = ReadinessStateMachine()
    monitor = ReadinessMonitor(
        state_machine=sm,
        warmup_orchestrator=_FakeWarmupOrchestrator(strategy),
        warmup_policy=warm_policy,
        bar_buffer=LiveBarBuffer(processor=lambda _bar: None),
        live_bar_processor=lambda _bar: None,
        data_source=ds,
        socketio_publisher=socketio,
        logger=FakeLogger(),
    )
    monitor.set_pair("MNQ")
    return monitor, strategy


class _RecordingDataSource:
    """Fresh history + records refresh requests."""

    def __init__(self) -> None:
        self.requests: list[int | None] = []

    def check_history_completeness(
        self, bars: list[dict[str, Any]] | None = None
    ) -> tuple[bool, str]:
        return True, "OK"

    def request_refresh(self, days: int | None = None) -> None:
        self.requests.append(days)


class _RetryCancellingDataSource:
    """Data source whose request_refresh can be asserted after a retry."""

    def __init__(self) -> None:
        self.requests: list[int | None] = []

    def check_history_completeness(
        self, bars: list[dict[str, Any]] | None = None
    ) -> tuple[bool, str]:
        return True, "OK"

    def request_refresh(self, days: int | None = None) -> None:
        self.requests.append(days)


def _join_warmup(monitor) -> None:
    """Wait for the background warmup thread to finish (test helper)."""
    if monitor._warmup_thread is not None:
        monitor._warmup_thread.join(timeout=5.0)


class TestReadinessMonitorEvents:
    def test_history_loaded_emitted_on_complete(self) -> None:
        socketio = DummySocketIO()
        monitor, _strategy = _make_monitor(
            socketio, _FreshDataSource(), _FakeWarmupPolicy(warm=False)
        )
        monitor._state_machine.connect()

        monitor.on_history_complete(_make_bars())
        _join_warmup(monitor)

        assert monitor._state_machine.state.name == "WARMING_UP"
        events = [event for event, _payload in socketio.events]
        assert "history_loaded" in events

    def test_trading_ready_emitted_when_fresh_and_warm(self) -> None:
        socketio = DummySocketIO()
        monitor, _strategy = _make_monitor(
            socketio, _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._state_machine.connect()

        monitor.on_history_complete(_make_bars())
        _join_warmup(monitor)

        assert monitor._state_machine.state.name == "READY"
        events = [event for event, _payload in socketio.events]
        assert "history_loaded" in events
        assert "trading_ready" in events

    def test_trading_ready_not_emitted_when_stale(self) -> None:
        """Market-closed case: bars exist but are too stale for trading."""
        socketio = DummySocketIO()
        monitor, _strategy = _make_monitor(
            socketio, _StaleDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._state_machine.connect()

        monitor.on_history_complete(_make_bars())
        _join_warmup(monitor)

        assert monitor._state_machine.state.name == "WARMING_UP"
        events = [event for event, _payload in socketio.events]
        assert "history_loaded" in events
        assert "trading_ready" not in events


class TestLiveBarRouting:
    def test_live_bar_dropped_before_history_complete(self) -> None:
        processed: list[dict[str, Any]] = []
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._live_bar_processor = processed.append
        monitor._state_machine.connect()

        monitor.on_live_bar(_make_bars(1)[0])
        assert processed == []
        assert monitor._state_machine.state.name == "CONNECTED"

    def test_live_bar_buffered_during_refresh(self) -> None:
        processed: list[dict[str, Any]] = []
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._live_bar_processor = processed.append
        monitor._state_machine.connect()
        monitor.on_refresh_start()

        bar = _make_bars(1)[0]
        monitor.on_live_bar(bar)
        assert processed == []
        assert len(monitor._bar_buffer) == 1

    def test_live_bar_processed_during_warmup_stays_warming(self) -> None:
        processed: list[dict[str, Any]] = []
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=False)
        )
        monitor._live_bar_processor = processed.append
        monitor._bar_buffer._processor = processed.append
        monitor._state_machine.connect()
        monitor.on_history_complete(_make_bars())

        assert monitor._state_machine.state.name == "WARMING_UP"
        bar = {"time": 9999, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}
        monitor.on_live_bar(bar)
        # The live bar is now fed to the strategy so indicators can keep warming,
        # but the state must stay WARMING_UP until the policy reports warm.
        assert [b["time"] for b in processed] == [9999]
        assert monitor._state_machine.state.name == "WARMING_UP"
        assert len(monitor._bar_buffer) == 0

    def test_buffered_bars_flushed_when_ready(self) -> None:
        processed: list[dict[str, Any]] = []
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=False)
        )
        monitor._live_bar_processor = processed.append
        monitor._bar_buffer._processor = processed.append
        monitor._state_machine.connect()
        monitor.on_history_complete(_make_bars())

        bar1 = {"time": 9999, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}
        bar2 = {"time": 10000, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}
        monitor.on_live_bar(bar1)
        # First live bar is processed while still warming up.
        assert monitor._state_machine.state.name == "WARMING_UP"
        assert [b["time"] for b in processed] == [9999]
        assert len(monitor._bar_buffer) == 0

        # Now make policy warm and re-trigger completion check via another live bar
        monitor._warmup_policy = _FakeWarmupPolicy(warm=True)
        monitor.on_live_bar(bar2)
        assert monitor._state_machine.state.name == "READY"
        assert len(processed) == 2
        assert [b["time"] for b in processed] == [9999, 10000]
        assert len(monitor._bar_buffer) == 0

    def test_refresh_start_clears_buffer(self) -> None:
        processed: list[dict[str, Any]] = []
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._live_bar_processor = processed.append
        monitor._state_machine.connect()
        monitor.on_refresh_start()

        bar = _make_bars(1)[0]
        monitor.on_live_bar(bar)
        assert len(monitor._bar_buffer) == 1

        monitor.on_refresh_start()
        assert len(monitor._bar_buffer) == 0
        assert processed == []


class TestEmptyHistoryRetry:
    def test_empty_history_schedules_retry(self) -> None:
        ds = _RetryCancellingDataSource()
        socketio = DummySocketIO()
        monitor, _strategy = _make_monitor(
            socketio, ds, _FakeWarmupPolicy(warm=True)
        )
        monitor._retry_base_delay_sec = 0.01
        monitor._retry_max_delay_sec = 0.01
        monitor._state_machine.connect()

        monitor.on_history_complete([])
        assert monitor._state_machine.state.name == "WAITING_FOR_HISTORY"

        # Wait for the retry timer to fire.
        import time
        time.sleep(0.05)
        assert len(ds.requests) == 1

    def test_real_history_cancels_retry(self) -> None:
        ds = _RetryCancellingDataSource()
        socketio = DummySocketIO()
        monitor, _strategy = _make_monitor(
            socketio, ds, _FakeWarmupPolicy(warm=True)
        )
        monitor._retry_base_delay_sec = 0.01
        monitor._retry_max_delay_sec = 0.01
        monitor._state_machine.connect()

        monitor.on_history_complete([])
        assert monitor._state_machine.state.name == "WAITING_FOR_HISTORY"

        # Before timer fires, real history arrives.
        monitor.on_history_complete(_make_bars())
        assert monitor._state_machine.state.name == "READY"

        import time
        time.sleep(0.05)
        # Retry should have been cancelled.
        assert len(ds.requests) == 0


class TestWarmupCancellation:
    def test_refresh_during_warmup_cancels_old_thread(self) -> None:
        """on_refresh_start() while warmup is running must cancel the old thread."""
        import threading

        started = threading.Event()
        blocked = threading.Event()

        class _SlowOrchestrator:
            """Orchestrator that blocks mid-replay until signalled."""

            def __init__(self, strategy: _FakeStrategy) -> None:
                self._strategy = strategy
                self._stop_event = threading.Event()

            @property
            def strategy(self) -> _FakeStrategy:
                return self._strategy

            def cancel(self) -> None:
                self._stop_event.set()

            def reset_cancel(self) -> None:
                self._stop_event.clear()

            def set_progress_listener(self, listener) -> None:
                pass

            def run(self, bars, pair) -> None:
                started.set()
                blocked.wait(timeout=5.0)  # stall until test signals it

        strategy = _FakeStrategy()
        sm = ReadinessStateMachine()
        orchestrator = _SlowOrchestrator(strategy)
        monitor = ReadinessMonitor(
            state_machine=sm,
            warmup_orchestrator=orchestrator,
            warmup_policy=_FakeWarmupPolicy(warm=True),
            bar_buffer=LiveBarBuffer(processor=lambda _: None),
            live_bar_processor=lambda _: None,
            data_source=_FreshDataSource(),
            logger=FakeLogger(),
        )
        monitor.set_pair("MNQ")
        sm.connect()

        # Start warmup — thread will block inside the slow orchestrator.
        monitor.on_history_complete(_make_bars())
        assert started.wait(timeout=2.0), "Warmup thread never started"
        assert monitor._warmup_in_progress is True

        # Signal: refresh arrives while warmup is blocked.
        blocked.set()  # unblock so cancel + join don't hang
        monitor.on_refresh_start()

        # After on_refresh_start returns, the old warmup thread must be gone.
        assert monitor._warmup_in_progress is False
        assert monitor._warmup_thread is None
        assert orchestrator._stop_event.is_set()
        assert sm.state.name == "REFRESHING"

    def test_second_history_load_cancels_first_warmup(self) -> None:
        """A second on_history_complete() must cancel the first warmup thread."""
        import threading

        first_started = threading.Event()
        first_blocked = threading.Event()

        class _SlowOrchestrator2:
            def __init__(self, strategy: _FakeStrategy) -> None:
                self._strategy = strategy
                self._stop_event = threading.Event()
                self.run_count = 0

            @property
            def strategy(self) -> _FakeStrategy:
                return self._strategy

            def cancel(self) -> None:
                self._stop_event.set()

            def reset_cancel(self) -> None:
                self._stop_event.clear()

            def set_progress_listener(self, listener) -> None:
                pass

            def run(self, bars, pair) -> None:
                self.run_count += 1
                if self.run_count == 1:
                    first_started.set()
                    first_blocked.wait(timeout=5.0)

        strategy = _FakeStrategy()
        sm = ReadinessStateMachine()
        orchestrator = _SlowOrchestrator2(strategy)
        monitor = ReadinessMonitor(
            state_machine=sm,
            warmup_orchestrator=orchestrator,
            warmup_policy=_FakeWarmupPolicy(warm=True),
            bar_buffer=LiveBarBuffer(processor=lambda _: None),
            live_bar_processor=lambda _: None,
            data_source=_FreshDataSource(),
            logger=FakeLogger(),
        )
        monitor.set_pair("MNQ")
        sm.connect()

        monitor.on_history_complete(_make_bars())
        assert first_started.wait(timeout=2.0)

        # Unblock so join() doesn't hang, then deliver second history.
        first_blocked.set()
        monitor.on_history_complete(_make_bars())

        # Both warmup runs should have happened (second one ran after cancel).
        _join_warmup(monitor)
        assert orchestrator.run_count == 2


class TestDegradeAndRecover:
    def test_gap_detected_degrades_ready_state(self) -> None:
        socketio = DummySocketIO()
        monitor, _strategy = _make_monitor(
            socketio, _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._state_machine.connect()
        monitor.on_history_complete(_make_bars())
        assert monitor._state_machine.state.name == "READY"

        monitor.on_gap_detected(120, "test gap")
        assert monitor._state_machine.state.name == "DEGRADED"

    def test_heartbeat_stale_degrades_live_state(self) -> None:
        socketio = DummySocketIO()
        monitor, _strategy = _make_monitor(
            socketio, _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._state_machine.connect()
        monitor.on_history_complete(_make_bars())
        monitor._state_machine.live_bar_received()
        assert monitor._state_machine.state.name == "LIVE"

        monitor.on_heartbeat_stale(91.0)
        assert monitor._state_machine.state.name == "DEGRADED"

    def test_recover_when_warm(self) -> None:
        processed: list[dict[str, Any]] = []
        monitor, _strategy = _make_monitor(
            socketio=DummySocketIO(),
            ds=_FreshDataSource(),
            warm_policy=_FakeWarmupPolicy(warm=True),
        )
        monitor._live_bar_processor = processed.append
        monitor._state_machine.connect()
        monitor.on_history_complete(_make_bars())
        monitor.on_gap_detected(120, "test gap")
        assert monitor._state_machine.state.name == "DEGRADED"

        bar = {"time": 9999, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}
        monitor.on_live_bar(bar)
        assert monitor._state_machine.state.name == "READY"
        assert len(processed) == 1

    def test_no_recover_when_not_warm(self) -> None:
        socketio = DummySocketIO()
        monitor, _strategy = _make_monitor(
            socketio, _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._state_machine.connect()
        monitor.on_history_complete(_make_bars())
        assert monitor._state_machine.state.name == "READY"

        monitor.on_gap_detected(120, "test gap")
        assert monitor._state_machine.state.name == "DEGRADED"

        # Simulate indicators cooling back down; live bars should not recover.
        monitor._warmup_policy = _FakeWarmupPolicy(warm=False)
        bar = {"time": 9999, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}
        monitor.on_live_bar(bar)
        assert monitor._state_machine.state.name == "DEGRADED"


class _FakeProgressEmitter:
    """Records phase/warmup progress emissions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit_warmup_progress(self, current: int, total: int) -> None:
        self.events.append(("warmup_progress", {"current": current, "total": total}))

    def emit_phase_started(self, phase: str, reason: str) -> None:
        self.events.append(("phase_started", {"phase": phase, "reason": reason}))


class TestReadinessMonitorProgress:
    def test_progress_tracker_updated_by_state_machine_observer(self) -> None:
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._state_machine.connect()
        assert monitor.get_health()["readiness_percent"] == 15

        monitor.on_history_complete(_make_bars())
        _join_warmup(monitor)

        health = monitor.get_health()
        assert health["readiness_state"] == "READY"
        assert health["readiness_percent"] == 95
        assert health["phase"] is None

    def test_phase_started_emitted_on_refresh_and_history(self) -> None:
        emitter = _FakeProgressEmitter()
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._progress_emitter = emitter
        monitor._state_machine.connect()

        monitor.on_refresh_start()
        monitor.on_history_complete(_make_bars())
        _join_warmup(monitor)

        phases = [e for e, _ in emitter.events if e == "phase_started"]
        assert "phase_started" in phases
        # Two phase_started events: refreshing and warmup.
        assert phases.count("phase_started") == 2

    def test_stale_history_surfaces_reason_in_health(self) -> None:
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _StaleDataSource(), _FakeWarmupPolicy(warm=True)
        )
        monitor._state_machine.connect()
        monitor.on_history_complete(_make_bars())
        _join_warmup(monitor)

        health = monitor.get_health()
        assert health["readiness_state"] == "WARMING_UP"
        assert "120m old" in health["readiness_reason"]


class TestConnectionChange:
    def test_on_connection_change_drives_state_machine(self) -> None:
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=True)
        )
        assert monitor._state_machine.state.name == "DISCONNECTED"

        monitor.on_connection_change(True)
        assert monitor._state_machine.state.name == "CONNECTED"

        monitor.on_connection_change(False)
        assert monitor._state_machine.state.name == "DISCONNECTED"


class _BlockingDataSource:
    """History that never passes completeness, with a configurable reason."""

    def __init__(self, reason: str) -> None:
        self._reason = reason
        self.requests: list[int | None] = []

    def check_history_completeness(
        self, bars: list[dict[str, Any]] | None = None
    ) -> tuple[bool, str]:
        return False, self._reason

    def request_refresh(self, days: int | None = None) -> None:
        self.requests.append(days)


class TestGapFill:
    """Auto gap-fill: a fillable hole in recent history triggers a bounded
    refresh; staleness does not."""

    @staticmethod
    def _drive_to_warming_up(monitor: ReadinessMonitor) -> None:
        monitor.on_connection_change(True)
        monitor.on_history_complete(_make_bars(40))
        _join_warmup(monitor)

    def test_gap_reason_requests_refresh_once(self) -> None:
        ds = _BlockingDataSource("Gap detected: 40m hole in last 2h")
        monitor, _ = _make_monitor(DummySocketIO(), ds, _FakeWarmupPolicy(warm=True))

        self._drive_to_warming_up(monitor)

        assert len(ds.requests) == 1
        assert monitor._gap_fill_in_flight is True

    def test_no_duplicate_request_while_fill_in_flight(self) -> None:
        ds = _BlockingDataSource("Gap detected: 40m hole in last 2h")
        monitor, _ = _make_monitor(DummySocketIO(), ds, _FakeWarmupPolicy(warm=True))
        self._drive_to_warming_up(monitor)

        # More live-bar-driven checks while the fill refresh is still running.
        monitor._try_warmup_complete()
        monitor._try_warmup_complete()

        assert len(ds.requests) == 1

    def test_refresh_start_clears_in_flight_and_allows_next_attempt(self) -> None:
        ds = _BlockingDataSource("Gap detected: 40m hole in last 2h")
        monitor, _ = _make_monitor(DummySocketIO(), ds, _FakeWarmupPolicy(warm=True))
        self._drive_to_warming_up(monitor)

        monitor.on_refresh_start()
        assert monitor._gap_fill_in_flight is False

        # The fill refresh completes but the hole is still there → attempt 2.
        monitor.on_history_complete(_make_bars(40))
        _join_warmup(monitor)

        assert len(ds.requests) == 2

    def test_gap_fill_capped_at_three_attempts(self) -> None:
        ds = _BlockingDataSource("Gap detected: 40m hole in last 2h")
        monitor, _ = _make_monitor(DummySocketIO(), ds, _FakeWarmupPolicy(warm=True))
        self._drive_to_warming_up(monitor)
        assert len(ds.requests) == 1

        for expected in (2, 3):
            monitor.on_refresh_start()
            monitor.on_history_complete(_make_bars(40))
            _join_warmup(monitor)
            assert len(ds.requests) == expected

        # Cap reached: further cycles must not request again.
        monitor.on_refresh_start()
        monitor.on_history_complete(_make_bars(40))
        _join_warmup(monitor)
        assert len(ds.requests) == 3

    def test_stale_reason_never_requests_refresh(self) -> None:
        ds = _BlockingDataSource("Last bar is 120m old (need < 1m)")
        monitor, _ = _make_monitor(DummySocketIO(), ds, _FakeWarmupPolicy(warm=True))

        self._drive_to_warming_up(monitor)
        monitor._try_warmup_complete()

        assert ds.requests == []

    def test_fresh_connection_resets_gap_fill_episode(self) -> None:
        ds = _BlockingDataSource("Gap detected: 40m hole in last 2h")
        monitor, _ = _make_monitor(DummySocketIO(), ds, _FakeWarmupPolicy(warm=True))
        self._drive_to_warming_up(monitor)
        assert monitor._gap_fill_attempts == 1

        monitor.on_connection_change(True)

        assert monitor._gap_fill_attempts == 0
        assert monitor._gap_fill_in_flight is False
