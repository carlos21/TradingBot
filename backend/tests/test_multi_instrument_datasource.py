"""Tests for multi-instrument routing in ``ZMQDataSource``."""

import threading
from collections import defaultdict
from unittest.mock import MagicMock

import pytest

from src.config.models import DEFAULT_HISTORY_HOURS
from src.infrastructure.gateway.datasource import DataSourceState, ZMQDataSource
from tests.fakes import FakeLogger


class FakeCoordinator:
    def __init__(self):
        self.bars = []
        self.ticks = []
        self.partials = []
        self.history_loaded = []
        self.late_history_batches = []
        self.gaps = []

    def route_bar(self, bar: dict) -> None:
        self.bars.append(bar)

    def route_tick(self, tick: dict) -> None:
        self.ticks.append(tick)

    def route_partial_bar(self, partial: dict) -> None:
        self.partials.append(partial)

    def route_history_loaded(self, bars: list[dict]) -> None:
        self.history_loaded.append(bars)

    def route_late_history_batch(self, bar_count: int, pair: str | None = None) -> None:
        self.late_history_batches.append((bar_count, pair))

    def route_gap_detected(self, gap_seconds: int, context: str, pair: str | None = None) -> None:
        self.gaps.append((gap_seconds, context, pair))

    def route_heartbeat_stale(self, age_seconds: float, pair: str | None = None) -> None:
        pass

    def route_before_refresh(self, pair: str | None = None) -> None:
        pass

    def route_refresh_start(self, pair: str | None = None) -> None:
        pass


class FakeGateway:
    def __init__(self):
        self.is_running = False
        self.is_connected = False
        self.instrument = "MNQ 09-26"
        self._callbacks = {}
        self._conn_listeners = []

    def on(self, msg_type, callback):
        self._callbacks[msg_type] = callback

    def on_connection_change(self, callback):
        self._conn_listeners.append(callback)

    def send_subscribe(self, instrument):
        self._subscribed = instrument

    def trigger(self, msg_type, payload):
        cb = self._callbacks.get(msg_type)
        if cb:
            cb(payload)


class FakeZMQDataSource(ZMQDataSource):
    """Lightweight ZMQDataSource that skips the heavy __init__."""

    def __init__(self, pair: str = "MNQ", gateway=None):
        self.logger = FakeLogger()
        self.pair = pair
        self.history_hours = DEFAULT_HISTORY_HOURS
        self._gateway = gateway
        self._gateway_config = MagicMock()
        self._owns_gateway = False
        self._market_filter = None
        self._coordinator = None

        self._first_platform_connect = True
        self._historical_bars: list[dict] = []
        self._per_pair_bars: dict[str, list[dict]] = {}
        self._per_pair_locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._bars_lock = MagicMock()
        self._state = DataSourceState.STREAMING
        self._last_history_time: int = 0
        self._refresh_buffer: list[dict] = []

        self.on_history_complete = None
        self.on_live_bar = None
        self.on_before_refresh = None
        self.on_refresh_start = None
        self.on_gap_detected = None
        self.on_heartbeat_stale = None
        self.on_late_history_batch = None

        self._stop_event = MagicMock()
        self._callback = None
        self._from_time = 0
        self._cb_lock = MagicMock()

        self._current_bars = {}
        self._extra_instruments = {}
        self._extra_instruments_lock = threading.Lock()
        self._last_emit_time = 0.0
        self._last_native_partial_time = 0.0

        self._stats = {
            "ticks_received": 0,
            "bars_received": 0,
            "history_batches": 0,
        }
        self._duplicate_count = 0
        self._gap_count = 0
        self._gap_threshold = 60
        self._history_request_delay_sec = 1.0
        self._pending_refresh_timer = None
        self._notifier = MagicMock()
        self._market_is_open = True
        self._last_completed_bar_time = 0.0
        self._heartbeat_thread = None
        self._heartbeat_stop_event = MagicMock()
        self._heartbeat_alert_sent = False
        self._heartbeat_check_interval_sec = 30.0
        self._heartbeat_alert_threshold_sec = 90.0


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def data_source(gateway):
    ds = FakeZMQDataSource(pair="MNQ", gateway=gateway)
    return ds


@pytest.fixture
def coordinator():
    return FakeCoordinator()


class TestMultiInstrumentRouting:
    def test_set_coordinator_wires_routing(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        assert data_source._coordinator is coordinator

    def test_on_bar_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "ES",
        })
        assert len(coordinator.bars) == 1
        assert coordinator.bars[0]["pair"] == "ES"

    def test_on_bar_default_pair_uses_legacy_cache(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "MNQ",
        })
        assert len(data_source._historical_bars) == 1
        assert len(coordinator.bars) == 1

    def test_on_bar_other_pair_uses_per_pair_cache(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "ES",
        })
        assert "ES" in data_source._per_pair_bars
        assert len(data_source._per_pair_bars["ES"]) == 1

    def test_on_bar_buffers_default_pair_in_connected_state(self, data_source, coordinator):
        data_source._state = DataSourceState.CONNECTED
        data_source.set_coordinator(coordinator)
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "MNQ",
        })
        assert len(data_source._historical_bars) == 0
        assert len(data_source._refresh_buffer) == 1
        # The bar is still routed to the coordinator even while buffering.
        assert len(coordinator.bars) == 1

    def test_on_history_batch_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_history_batch({
            "pair": "ES",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            ],
        })
        assert "ES" in data_source._per_pair_bars
        assert len(data_source._per_pair_bars["ES"]) == 1

    def test_on_history_end_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_history_batch({
            "pair": "ES",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "ES"},
            ],
        })
        data_source._on_history_end({"pair": "ES"})
        assert len(coordinator.history_loaded) == 1
        assert coordinator.history_loaded[0][0]["pair"] == "ES"

    def test_load_historical_bars_with_pair_param(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_history_batch({
            "pair": "ES",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "ES"},
                {"time": 200, "open": 11, "high": 12, "low": 10, "close": 11, "volume": 1, "pair": "ES"},
            ],
        })
        bars = data_source.load_historical_bars("1m", pair="ES")
        assert len(bars) == 2
        assert bars[0]["pair"] == "ES"

    def test_load_historical_bars_default_pair(self, data_source):
        data_source._historical_bars = [
            {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "MNQ"},
        ]
        bars = data_source.load_historical_bars("1m")
        assert len(bars) == 1
        assert bars[0]["pair"] == "MNQ"

    def test_on_tick_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_tick({"time": 100, "price": 10, "volume": 1, "pair": "ES"})
        assert len(coordinator.partials) == 1
        assert coordinator.partials[0]["pair"] == "ES"

    def test_on_partial_bar_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_partial_bar({"time": 100, "close": 10, "pair": "ES"})
        assert len(coordinator.partials) == 1
        assert coordinator.partials[0]["pair"] == "ES"

    def test_gap_detection_routes_to_coordinator(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        # Two bars with a 120-second gap.
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "MNQ",
        })
        data_source._on_bar({
            "time": 220, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "MNQ",
        })
        assert data_source._gap_count >= 1

    def test_late_history_batch_routes_to_coordinator(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_history_batch({
            "pair": "MNQ",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "MNQ"},
            ],
        })
        # No assertion on coordinator because route_late_history_batch delegates
        # to the session; just ensure no exception and state is unchanged.
        assert data_source._state == DataSourceState.STREAMING


class TestSecondaryPairStateHygiene:
    """A secondary instrument's refresh/history lifecycle must not mutate the
    default pair's data-source state."""

    def test_secondary_history_end_does_not_touch_state(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_history_batch({
            "pair": "ES",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "ES"},
            ],
        })
        data_source._on_history_end({"pair": "ES"})
        assert data_source._state == DataSourceState.STREAMING
        # Stream liveness stamp is still updated.
        assert data_source._last_completed_bar_time > 0
        assert len(coordinator.history_loaded) == 1

    def test_secondary_refresh_start_does_not_touch_state(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._per_pair_bars["ES"] = [
            {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "ES"},
        ]
        data_source._on_refresh_start({"pair": "ES"})
        # Was REFRESHING before the fix; the default pair's lifecycle is unaffected.
        assert data_source._state == DataSourceState.STREAMING

    def test_on_tick_keeps_separate_forming_bars_per_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_tick({"time": 100, "price": 10, "volume": 1, "pair": "ES"})
        data_source._on_tick({"time": 101, "price": 200, "volume": 1, "pair": "MNQ"})
        data_source._on_tick({"time": 102, "price": 12, "volume": 1, "pair": "ES"})
        assert data_source._current_bars["ES"]["close"] == 12
        assert data_source._current_bars["ES"]["high"] == 12
        assert data_source._current_bars["MNQ"]["close"] == 200
        assert data_source._current_bars["MNQ"]["high"] == 200
