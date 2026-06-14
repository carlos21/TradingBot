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

    def test_live_bar_buffered_during_warmup(self) -> None:
        processed: list[dict[str, Any]] = []
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=False)
        )
        monitor._live_bar_processor = processed.append
        monitor._state_machine.connect()
        monitor.on_history_complete(_make_bars())

        assert monitor._state_machine.state.name == "WARMING_UP"
        bar = {"time": 9999, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}
        monitor.on_live_bar(bar)
        assert processed == []
        assert len(monitor._bar_buffer) == 1

    def test_buffered_bars_flushed_when_ready(self) -> None:
        processed: list[dict[str, Any]] = []
        monitor, _strategy = _make_monitor(
            DummySocketIO(), _FreshDataSource(), _FakeWarmupPolicy(warm=False)
        )
        monitor._live_bar_processor = processed.append
        monitor._bar_buffer._processor = processed.append
        monitor._state_machine.connect()
        monitor.on_history_complete(_make_bars())

        bar = {"time": 9999, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}
        monitor.on_live_bar(bar)
        assert len(monitor._bar_buffer) == 1

        # Now make policy warm and re-trigger completion check via another live bar
        monitor._warmup_policy = _FakeWarmupPolicy(warm=True)
        monitor.on_live_bar(bar)
        assert monitor._state_machine.state.name == "READY"
        assert len(processed) == 2
        assert all(b["time"] == 9999 for b in processed)
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
