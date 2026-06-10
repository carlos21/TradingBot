"""Tests for src/gateway/datasource.py.

This module tests the ZMQDataSource class, which is a CombinedDataSource
implementation that receives market data from trading platforms via ZeroMQ.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.config.models import DEFAULT_HISTORY_DAYS
from src.infrastructure.gateway.datasource import DataSourceState, ZMQDataSource
from src.infrastructure.gateway.gateway import GatewayConfig, TradingGateway
from src.infrastructure.gateway.protocol import MessageType
from tests.fakes import FakeLogger


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return FakeLogger()


@pytest.fixture
def mock_gateway():
    """Return a mocked TradingGateway with callback storage."""
    gateway = MagicMock(spec=TradingGateway)
    gateway.is_connected = True
    gateway.pair = "MNQ"

    # Store registered callbacks by message type
    gateway._callbacks = {}

    def on_side_effect(msg_type, callback):
        gateway._callbacks.setdefault(msg_type, []).append(callback)

    gateway.on.side_effect = on_side_effect
    gateway.start = MagicMock()
    gateway.stop = MagicMock()
    gateway.send_refresh_request = MagicMock()
    return gateway


@pytest.fixture
def data_source(logger, mock_gateway):
    """Return a ZMQDataSource wired to a mocked gateway."""
    ds = ZMQDataSource(logger=logger, gateway=mock_gateway, pair="MNQ")
    return ds


def make_tick(time_val: int, price: float, volume: int = 1, pair: str = "MNQ") -> dict:
    return {"time": time_val, "price": price, "volume": volume, "pair": pair}


def make_bar(
    time_val: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 100,
    pair: str = "MNQ",
) -> dict:
    return {
        "time": time_val,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "pair": pair,
    }


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestInitialization:

    def test_init_defaults(self, logger):
        ds = ZMQDataSource(logger=logger)
        assert ds.pair == "MNQ"
        assert ds.history_days == DEFAULT_HISTORY_DAYS
        assert ds.logger is logger
        assert ds._gateway is None
        assert ds._owns_gateway is True
        assert ds._historical_bars == []
        assert ds.state == DataSourceState.DISCONNECTED
        assert ds._last_history_time == 0
        assert ds._refresh_buffer == []
        assert ds.on_history_complete is None
        assert ds.on_live_bar is None
        assert ds.on_before_refresh is None
        assert ds._callback is None
        assert ds._from_time == 0
        assert ds._current_bar is None
        assert ds._last_emit_time == 0.0
        assert ds._last_native_partial_time == 0.0
        assert ds._stats == {
            "ticks_received": 0,
            "bars_received": 0,
            "history_batches": 0,
        }
        assert ds._gap_threshold == 60

    def test_init_with_gateway(self, logger, mock_gateway):
        ds = ZMQDataSource(logger=logger, gateway=mock_gateway, pair="ES")
        assert ds.pair == "ES"
        assert ds._gateway is mock_gateway
        assert ds._owns_gateway is False

    def test_init_with_config(self, logger):
        config = GatewayConfig(heartbeat_interval_sec=10.0)
        ds = ZMQDataSource(logger=logger, gateway_config=config)
        assert ds._gateway_config is config

    def test_ensure_gateway_creates_new(self, logger):
        ds = ZMQDataSource(logger=logger)
        with patch("src.infrastructure.gateway.datasource.TradingGateway") as MockGW:
            mock_gw = MagicMock(spec=TradingGateway)
            MockGW.return_value = mock_gw
            gw = ds._ensure_gateway()
            assert gw is mock_gw
            assert ds._owns_gateway is True
            MockGW.assert_called_once_with(
                logger,
                config=ds._gateway_config,
                pair="MNQ",
            )

    def test_ensure_gateway_returns_existing(self, data_source, mock_gateway):
        gw = data_source._ensure_gateway()
        assert gw is mock_gateway


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:

    def test_is_live(self, data_source):
        assert data_source.is_live is False
        data_source._state = DataSourceState.LIVE
        assert data_source.is_live is True

    def test_state(self, data_source):
        assert data_source.state == DataSourceState.DISCONNECTED
        data_source._state = DataSourceState.REFRESHING
        assert data_source.state == DataSourceState.REFRESHING

    def test_is_connected_with_gateway(self, data_source, mock_gateway):
        mock_gateway.is_connected = True
        assert data_source.is_connected is True
        mock_gateway.is_connected = False
        assert data_source.is_connected is False

    def test_is_connected_without_gateway(self, logger):
        ds = ZMQDataSource(logger=logger)
        assert ds.is_connected is False

    def test_gateway_property(self, data_source, mock_gateway):
        assert data_source.gateway is mock_gateway

    def test_stats(self, data_source):
        data_source._stats["ticks_received"] = 5
        data_source._stats["bars_received"] = 3
        assert data_source.stats == {"ticks_received": 5, "bars_received": 3, "history_batches": 0}
        # Ensure a copy is returned
        s = data_source.stats
        s["ticks_received"] = 999
        assert data_source._stats["ticks_received"] == 5


# ---------------------------------------------------------------------------
# Platform connection / disconnection
# ---------------------------------------------------------------------------


class TestPlatformConnection:

    def test_on_platform_connected_schedules_delayed_refresh(self, data_source, mock_gateway):
        data_source._history_request_delay_sec = 0.1  # 100ms for test speed
        data_source.on_platform_connected()
        assert data_source.state == DataSourceState.CONNECTED
        assert data_source._pending_refresh_timer is not None
        # Wait for the timer to fire
        data_source._pending_refresh_timer.join()
        mock_gateway.send_refresh_request.assert_called_once()

    def test_on_platform_connected_ignored_if_not_disconnected(self, data_source, mock_gateway):
        data_source._state = DataSourceState.CONNECTED
        data_source.on_platform_connected()
        mock_gateway.send_refresh_request.assert_not_called()

    def test_on_platform_disconnected_cancels_pending_timer(self, data_source):
        data_source._history_request_delay_sec = 10.0
        data_source.on_platform_connected()
        assert data_source._pending_refresh_timer is not None
        data_source.on_platform_disconnected()
        assert data_source._pending_refresh_timer is None
        assert data_source.state == DataSourceState.DISCONNECTED

    def test_delayed_refresh_aborted_if_disconnected(self, data_source, mock_gateway):
        data_source._history_request_delay_sec = 0.1
        data_source.on_platform_connected()
        timer = data_source._pending_refresh_timer
        assert timer is not None
        # Disconnect before timer fires
        data_source.on_platform_disconnected()
        # Wait to ensure the timer callback does not run (or aborts cleanly)
        timer.join(timeout=0.5)
        mock_gateway.send_refresh_request.assert_not_called()


# ---------------------------------------------------------------------------
# Start / Stop / Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:

    def test_start_registers_callbacks(self, data_source, mock_gateway):
        data_source.start()
        mock_gateway.start.assert_called_once()
        assert MessageType.TICK in mock_gateway._callbacks
        assert MessageType.BAR in mock_gateway._callbacks
        assert MessageType.PARTIAL_BAR in mock_gateway._callbacks
        assert MessageType.HISTORY_BATCH in mock_gateway._callbacks
        assert MessageType.HISTORY_END in mock_gateway._callbacks
        assert MessageType.REFRESH_START in mock_gateway._callbacks

    def test_start_ensures_gateway(self, logger):
        ds = ZMQDataSource(logger=logger)
        with patch("src.infrastructure.gateway.datasource.TradingGateway") as MockGW:
            mock_gw = MagicMock(spec=TradingGateway)
            MockGW.return_value = mock_gw
            ds.start()
            mock_gw.start.assert_called_once()

    def test_stop_owns_gateway(self, logger):
        mock_gw = MagicMock(spec=TradingGateway)
        ds = ZMQDataSource(logger=logger)
        ds._gateway = mock_gw
        ds._owns_gateway = True
        ds.stop()
        mock_gw.stop.assert_called_once()

    def test_stop_does_not_stop_external_gateway(self, logger, mock_gateway):
        ds = ZMQDataSource(logger=logger, gateway=mock_gateway)
        ds.stop()
        # In datasource.py stop(): if self._owns_gateway and self._gateway: self._gateway.stop()
        # Since _owns_gateway is False, stop should NOT be called.
        mock_gateway.stop.assert_not_called()

    def test_shutdown_sets_stop_event_and_stops_gateway(self, logger):
        mock_gw = MagicMock(spec=TradingGateway)
        ds = ZMQDataSource(logger=logger)
        ds._gateway = mock_gw
        ds._owns_gateway = True
        ds.shutdown()
        assert ds._stop_event.is_set()
        mock_gw.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Tick handling & bar accumulation
# ---------------------------------------------------------------------------


class TestTickHandling:

    def test_on_tick_creates_current_bar(self, data_source):
        tick = make_tick(time_val=1000, price=5000.0, volume=10)
        data_source._on_tick(tick)
        assert data_source._stats["ticks_received"] == 1
        cb = data_source._current_bar
        assert cb is not None
        assert cb["time"] == (1000 // 60) * 60
        assert cb["open"] == 5000.0
        assert cb["high"] == 5000.0
        assert cb["low"] == 5000.0
        assert cb["close"] == 5000.0
        assert cb["volume"] == 10
        assert cb["pair"] == "MNQ"

    def test_on_tick_updates_existing_bar(self, data_source):
        data_source._on_tick(make_tick(time_val=1000, price=5000.0, volume=10))
        data_source._on_tick(make_tick(time_val=1001, price=5100.0, volume=5))
        cb = data_source._current_bar
        assert cb["high"] == 5100.0
        assert cb["low"] == 5000.0
        assert cb["close"] == 5100.0
        assert cb["volume"] == 15

    def test_on_tick_resets_on_minute_boundary(self, data_source):
        # Two ticks in different minutes
        data_source._on_tick(make_tick(time_val=60, price=100.0))
        data_source._on_tick(make_tick(time_val=120, price=200.0))
        cb = data_source._current_bar
        assert cb["time"] == 120
        assert cb["open"] == 200.0
        assert cb["high"] == 200.0

    def test_on_tick_uses_pair_from_source_when_missing(self, data_source):
        tick = {"time": 1000, "price": 5000.0, "volume": 1}
        data_source._on_tick(tick)
        assert data_source._current_bar["pair"] == "MNQ"

    def test_on_tick_emits_partial_after_one_second(self, data_source):
        live_bars = []
        data_source.on_live_bar = lambda bar: live_bars.append(bar)

        base_time = 1000000.0
        with patch("src.infrastructure.gateway.datasource.time.monotonic", return_value=base_time):
            data_source._on_tick(make_tick(time_val=1000, price=5000.0))
            # First tick always emits because _last_emit_time is 0
            assert len(live_bars) == 1
            assert live_bars[0].get("partial") is True

        # Next tick within 1 second should not emit
        with patch("src.infrastructure.gateway.datasource.time.monotonic", return_value=base_time + 0.5):
            data_source._on_tick(make_tick(time_val=1001, price=5100.0))
            assert len(live_bars) == 1

        # After 1 second, should emit again
        with patch("src.infrastructure.gateway.datasource.time.monotonic", return_value=base_time + 1.5):
            data_source._on_tick(make_tick(time_val=1002, price=5200.0))
            assert len(live_bars) == 2
            assert live_bars[1]["close"] == 5200.0

    def test_on_tick_skips_partial_when_native_recent(self, data_source):
        live_bars = []
        data_source.on_live_bar = lambda bar: live_bars.append(bar)
        base_time = 1000000.0

        # Set a recent native partial time
        data_source._last_native_partial_time = base_time

        with patch("src.infrastructure.gateway.datasource.time.monotonic", return_value=base_time + 1.0):
            data_source._on_tick(make_tick(time_val=1000, price=5000.0))
            # Should skip because native partial was < 2 seconds ago
            assert len(live_bars) == 0

        # After 2+ seconds from native partial, should emit
        with patch("src.infrastructure.gateway.datasource.time.monotonic", return_value=base_time + 2.5):
            data_source._on_tick(make_tick(time_val=1001, price=5100.0))
            assert len(live_bars) == 1


# ---------------------------------------------------------------------------
# Bar handling
# ---------------------------------------------------------------------------


class TestBarHandling:

    def test_on_bar_appends_in_order(self, data_source):
        data_source._on_bar(make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5))
        data_source._on_bar(make_bar(time_val=200, open_=10.5, high=12.0, low=10.0, close=11.0))
        assert len(data_source._historical_bars) == 2
        assert data_source._historical_bars[0]["time"] == 100
        assert data_source._historical_bars[1]["time"] == 200
        assert data_source._stats["bars_received"] == 2

    def test_on_bar_inserts_out_of_order(self, data_source, logger):
        data_source._on_bar(make_bar(time_val=200, open_=10.5, high=12.0, low=10.0, close=11.0))
        data_source._on_bar(make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5))
        assert len(data_source._historical_bars) == 2
        assert data_source._historical_bars[0]["time"] == 100
        assert data_source._historical_bars[1]["time"] == 200

    def test_on_bar_skips_duplicate(self, data_source):
        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._on_bar(bar)
        data_source._on_bar(bar)
        assert len(data_source._historical_bars) == 1

    def test_on_bar_during_refresh_buffers(self, data_source):
        data_source._state = DataSourceState.REFRESHING
        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._on_bar(bar)
        assert len(data_source._historical_bars) == 0
        assert len(data_source._refresh_buffer) == 1
        assert data_source._refresh_buffer[0]["time"] == 100

    def test_on_bar_during_connected_is_buffered(self, data_source):
        data_source._state = DataSourceState.CONNECTED
        received = []
        data_source.on_live_bar = lambda bar: received.append(bar)
        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._on_bar(bar)
        assert len(data_source._historical_bars) == 0
        assert len(data_source._refresh_buffer) == 1
        assert data_source._refresh_buffer[0]["time"] == 100
        assert len(received) == 0

    def test_on_bar_during_connected_and_refreshing_flushed_on_history_end(self, data_source):
        now = int(time.time())
        data_source._state = DataSourceState.CONNECTED
        data_source._historical_bars = [
            make_bar(time_val=now - 300, open_=10.0, high=11.0, low=9.0, close=10.5),
        ]
        # Bar arrives during CONNECTED (will be covered by history)
        data_source._on_bar(make_bar(time_val=now - 200, open_=11.0, high=12.0, low=10.0, close=11.5))
        # Transition to REFRESHING — buffer should be preserved
        data_source._on_refresh_start()
        assert data_source._state == DataSourceState.REFRESHING
        assert len(data_source._refresh_buffer) == 1
        # Bar arrives during REFRESHING (genuinely new)
        data_source._on_bar(make_bar(time_val=now - 100, open_=12.0, high=13.0, low=11.0, close=12.5))
        assert len(data_source._refresh_buffer) == 2
        # History batch includes the first two bars
        data_source._on_history_batch({
            "bars": [
                {"time": now - 300, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100},
                {"time": now - 200, "open": 11.0, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 100},
            ],
            "days": 1,
            "pair": "MNQ",
        })
        live_bars = []
        data_source.on_live_bar = lambda bar: live_bars.append(bar)
        data_source._on_history_end()
        assert data_source.state == DataSourceState.LIVE
        # now-200 is a duplicate (in history) — skipped. now-100 is new — emitted.
        assert len(data_source._historical_bars) == 3
        assert len(live_bars) == 1
        assert live_bars[0]["time"] == now - 100
        assert data_source._refresh_buffer == []

    def test_on_bar_triggers_live_bar_callback(self, data_source):
        received = []
        data_source.on_live_bar = lambda bar: received.append(bar)
        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._on_bar(bar)
        assert len(received) == 1
        assert received[0]["time"] == 100
        assert "partial" not in received[0]

    def test_on_bar_gap_detection(self, data_source, logger):
        # Create a gap > 120 seconds
        data_source._on_bar(make_bar(time_val=0, open_=10.0, high=11.0, low=9.0, close=10.5))
        data_source._on_bar(make_bar(time_val=200, open_=11.0, high=12.0, low=10.0, close=11.5))
        # The logger should have received a gap warning (we don't assert exact message,
        # just that the code path ran without error and bars are stored)
        assert len(data_source._historical_bars) == 2

    def test_on_bar_updates_existing_bar_when_values_differ(self, data_source):
        """A live bar with different OHLCV values should update the cache entry."""
        logger = RecordingLogger()
        data_source.logger = logger
        data_source._market_is_open = True

        hist_bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5, volume=100)
        live_bar = make_bar(time_val=100, open_=10.0, high=12.0, low=8.5, close=11.0, volume=200)

        # History batch arrives first
        data_source._on_history_batch({"bars": [hist_bar], "days": 1})
        assert data_source._historical_bars[0]["close"] == 10.5
        assert data_source._historical_bars[0]["volume"] == 100

        # Live bar arrives with different values — should update cache
        data_source._on_bar(live_bar)
        assert len(data_source._historical_bars) == 1
        assert data_source._historical_bars[0]["high"] == 12.0
        assert data_source._historical_bars[0]["low"] == 8.5
        assert data_source._historical_bars[0]["close"] == 11.0
        assert data_source._historical_bars[0]["volume"] == 200
        assert data_source._duplicate_count == 0
        assert any("[LiveUpdate]" in m for m in logger.messages)
        assert not any("Duplicate bar" in m for m in logger.messages)

    def test_on_bar_warns_on_identical_duplicate_when_market_open(self, data_source):
        """A live bar with identical values is counted as duplicate and warned when market is open."""
        logger = RecordingLogger()
        data_source.logger = logger
        data_source._market_is_open = True

        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._on_history_batch({"bars": [bar], "days": 1})

        # Identical live bar — duplicate warning (market is open)
        data_source._on_bar(bar)
        assert len(data_source._historical_bars) == 1
        assert data_source._duplicate_count == 1
        assert not any("[LiveUpdate]" in m for m in logger.messages)
        assert any("Duplicate bar" in m for m in logger.messages)

    def test_on_bar_silently_skips_identical_duplicate_when_market_closed(self, data_source):
        """A live bar with identical values is silently skipped when market is closed."""
        logger = RecordingLogger()
        data_source.logger = logger
        data_source._market_is_open = False

        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._on_history_batch({"bars": [bar], "days": 1})

        # Identical live bar — silently skipped (market is closed)
        data_source._on_bar(bar)
        assert len(data_source._historical_bars) == 1
        assert data_source._duplicate_count == 1
        assert not any("[LiveUpdate]" in m for m in logger.messages)
        assert not any("Duplicate bar" in m for m in logger.messages)


# ---------------------------------------------------------------------------
# Partial bar handling
# ---------------------------------------------------------------------------


class TestPartialBarHandling:

    def test_on_partial_bar_updates_native_time(self, data_source):
        base_time = 1000000.0
        with patch("src.infrastructure.gateway.datasource.time.monotonic", return_value=base_time):
            data_source._on_partial_bar({"time": 100, "open": 10.0, "close": 10.5})
            assert data_source._last_native_partial_time == base_time

    def test_on_partial_bar_emits_to_callback(self, data_source):
        received = []
        data_source.on_live_bar = lambda bar: received.append(bar)
        payload = {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 50}
        data_source._on_partial_bar(payload)
        assert len(received) == 1
        assert received[0]["partial"] is True
        assert received[0]["time"] == 100

    def test_on_partial_bar_during_connected_is_dropped(self, data_source):
        data_source._state = DataSourceState.CONNECTED
        received = []
        data_source.on_live_bar = lambda bar: received.append(bar)
        data_source._on_partial_bar({"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 50})
        assert len(received) == 0
        assert data_source._last_native_partial_time == 0.0

    def test_on_partial_bar_during_refreshing_is_dropped(self, data_source):
        data_source._state = DataSourceState.REFRESHING
        received = []
        data_source.on_live_bar = lambda bar: received.append(bar)
        data_source._on_partial_bar({"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 50})
        assert len(received) == 0
        assert data_source._last_native_partial_time == 0.0


# ---------------------------------------------------------------------------
# History batch handling
# ---------------------------------------------------------------------------


class TestHistoryBatchHandling:

    def test_on_history_batch_adds_bars(self, data_source):
        payload = {
            "bars": [
                {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100},
                {"time": 200, "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.0, "volume": 200},
            ],
            "days": 1,
            "pair": "MNQ",
        }
        data_source._on_history_batch(payload)
        assert len(data_source._historical_bars) == 2
        assert data_source._stats["history_batches"] == 1
        assert data_source._historical_bars[0]["time"] == 100
        assert data_source._historical_bars[1]["time"] == 200

    def test_on_history_batch_deduplicates(self, data_source):
        payload = {
            "bars": [
                {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100},
            ],
            "days": 1,
        }
        data_source._on_history_batch(payload)
        data_source._on_history_batch(payload)
        assert len(data_source._historical_bars) == 1

    def test_on_history_batch_sorts_bars(self, data_source):
        payload = {
            "bars": [
                {"time": 300, "open": 12.0, "high": 13.0, "low": 11.0, "close": 12.5, "volume": 300},
                {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100},
            ],
            "days": 1,
        }
        data_source._on_history_batch(payload)
        assert data_source._historical_bars[0]["time"] == 100
        assert data_source._historical_bars[1]["time"] == 300

    def test_on_history_batch_uses_default_pair(self, data_source):
        payload = {
            "bars": [
                {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100},
            ],
            "days": 1,
        }
        data_source._on_history_batch(payload)
        assert data_source._historical_bars[0]["pair"] == "MNQ"


# ---------------------------------------------------------------------------
# History end handling
# ---------------------------------------------------------------------------


class TestHistoryEndHandling:

    def test_on_history_end_sets_live_mode(self, data_source):
        data_source._on_history_end()
        assert data_source.state == DataSourceState.LIVE

    def test_on_history_end_calls_callback(self, data_source):
        called_with = []
        data_source.on_history_complete = lambda bars: called_with.append(bars)
        data_source._historical_bars = [make_bar(time_val=int(time.time()) - 300, open_=10.0, high=11.0, low=9.0, close=10.5)]
        data_source._on_history_end()
        assert len(called_with) == 1
        assert len(called_with[0]) == 1

    def test_on_history_end_callback_error_logged(self, data_source, logger):
        data_source.on_history_complete = lambda bars: (_ for _ in ()).throw(RuntimeError("boom"))
        data_source._historical_bars = [make_bar(time_val=int(time.time()) - 300, open_=10.0, high=11.0, low=9.0, close=10.5)]
        # Should not raise
        data_source._on_history_end()
        assert data_source.is_live is True

    def test_on_history_end_flushes_refresh_buffer(self, data_source):
        data_source._state = DataSourceState.REFRESHING
        data_source._refresh_buffer = [
            make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=200, open_=11.0, high=12.0, low=10.0, close=11.5),
        ]
        live_bars = []
        data_source.on_live_bar = lambda bar: live_bars.append(bar)
        data_source._on_history_end()
        assert len(data_source._historical_bars) == 2
        assert data_source._refresh_buffer == []
        assert len(live_bars) == 2

    def test_on_history_end_sets_last_history_time(self, data_source):
        now = int(time.time())
        data_source._historical_bars = [
            make_bar(time_val=now - 400, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=now - 300, open_=11.0, high=12.0, low=10.0, close=11.5),
        ]
        data_source._on_history_end()
        assert data_source._last_history_time == now - 300

    def test_on_history_end_scans_for_gaps(self, data_source):
        now = int(time.time())
        data_source._historical_bars = [
            make_bar(time_val=now - 400, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=now - 200, open_=11.0, high=12.0, low=10.0, close=11.5),
        ]
        data_source._on_history_end()
        assert data_source.is_live is True

    def test_on_history_end_stale_switches_to_live_without_retry(self, data_source, mock_gateway):
        """Stale history should switch to LIVE without sending retry refresh requests."""
        now = int(time.time())
        data_source._historical_bars = [
            make_bar(time_val=now - 1000, open_=10.0, high=11.0, low=9.0, close=10.5),
        ]
        data_source._on_history_end()
        assert data_source.state == DataSourceState.LIVE
        mock_gateway.send_refresh_request.assert_not_called()

    def test_on_history_batch_in_live_emits_history_ready_when_complete(self, data_source):
        """Gap-fill batches arriving in LIVE state should emit history_ready when history becomes complete."""
        now = int(time.time())
        called_with = []
        data_source.on_history_complete = lambda bars: called_with.append(list(bars))
        data_source._state = DataSourceState.LIVE
        data_source._history_complete = False
        data_source._history_complete_reason = "Last bar is old"

        # Seed with bars that have a continuous recent sequence but the last bar is old
        # (simulates stale history where recent data is missing)
        data_source._historical_bars = [
            make_bar(time_val=now - 180, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=now - 120, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=now - 60, open_=10.0, high=11.0, low=9.0, close=10.5),
        ]

        # Inject the missing recent bar via history_batch (simulates gap-fill)
        data_source._on_history_batch({
            'bars': [{
                'time': now,
                'open': 10.0,
                'high': 11.0,
                'low': 9.0,
                'close': 10.5,
                'volume': 100,
                'pair': 'MNQ',
            }],
            'pair': 'MNQ',
        })

        assert data_source._history_complete is True
        assert len(called_with) == 1
        assert len(called_with[0]) == 4

    def test_on_bar_in_live_emits_history_ready_when_becomes_complete(self, data_source):
        """Live bar that completes history should emit history_ready."""
        now = int(time.time())
        called_with = []
        data_source.on_history_complete = lambda bars: called_with.append(list(bars))
        data_source._state = DataSourceState.LIVE
        data_source._history_complete = False

        # Seed with continuous recent bars but missing the very last one
        data_source._historical_bars = [
            make_bar(time_val=now - 180, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=now - 120, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=now - 60, open_=10.0, high=11.0, low=9.0, close=10.5),
        ]

        # Inject the missing recent live bar
        data_source._on_bar({
            'time': now,
            'open': 10.0,
            'high': 11.0,
            'low': 9.0,
            'close': 10.5,
            'volume': 100,
            'pair': 'MNQ',
        })

        assert data_source._history_complete is True
        assert len(called_with) == 1
        assert len(called_with[0]) == 4

    def test_on_history_batch_does_not_emit_during_refreshing(self, data_source):
        """History batches during REFRESHING state should not emit history_ready."""
        now = int(time.time())
        called_with = []
        data_source.on_history_complete = lambda bars: called_with.append(list(bars))
        data_source._state = DataSourceState.REFRESHING
        data_source._history_complete = False

        data_source._on_history_batch({
            'bars': [{
                'time': now,
                'open': 10.0,
                'high': 11.0,
                'low': 9.0,
                'close': 10.5,
                'volume': 100,
                'pair': 'MNQ',
            }],
            'pair': 'MNQ',
        })

        assert data_source._history_complete is False  # not checked during REFRESHING
        assert len(called_with) == 0


# ---------------------------------------------------------------------------
# Refresh start handling
# ---------------------------------------------------------------------------


class TestRefreshStartHandling:

    def test_on_refresh_start_clears_recent_data(self, data_source):
        now = int(time.time())
        old_bar = make_bar(time_val=now - 90000, open_=10.0, high=11.0, low=9.0, close=10.5)
        new_bar = make_bar(time_val=now - 100, open_=11.0, high=12.0, low=10.0, close=11.5)
        data_source._historical_bars = [old_bar, new_bar]
        data_source._state = DataSourceState.LIVE
        data_source._current_bar = {"time": now}

        data_source._on_refresh_start()

        assert len(data_source._historical_bars) == 1
        assert data_source._historical_bars[0]["time"] == old_bar["time"]
        assert data_source.state == DataSourceState.REFRESHING
        assert data_source._current_bar is None

    def test_on_refresh_start_calls_before_refresh_callback(self, data_source):
        called = []
        data_source.on_before_refresh = lambda: called.append(1)
        data_source._on_refresh_start()
        assert called == [1]

    def test_on_refresh_start_callback_error_logged(self, data_source, logger):
        data_source.on_before_refresh = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        # Should not raise
        data_source._on_refresh_start()
        assert data_source.state == DataSourceState.REFRESHING

    def test_on_refresh_start_sets_last_history_time(self, data_source):
        now = int(time.time())
        old_bar = make_bar(time_val=now - 90000, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._historical_bars = [old_bar]
        data_source._on_refresh_start()
        assert data_source._last_history_time == old_bar["time"]

    def test_on_refresh_start_empty_history(self, data_source):
        data_source._historical_bars = []
        data_source._on_refresh_start()
        assert data_source._last_history_time == 0

    def test_on_refresh_start_preserves_buffer_when_from_connected(self, data_source):
        data_source._state = DataSourceState.CONNECTED
        data_source._refresh_buffer = [make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)]
        data_source._on_refresh_start()
        assert data_source.state == DataSourceState.REFRESHING
        assert len(data_source._refresh_buffer) == 1
        assert data_source._refresh_buffer[0]["time"] == 100


# ---------------------------------------------------------------------------
# Historical queries
# ---------------------------------------------------------------------------


class TestHistoricalQueries:

    def test_load_historical_bars_returns_copy(self, data_source):
        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._historical_bars = [bar]
        result = data_source.load_historical_bars()
        assert result == [bar]
        # Mutating result should not affect internal storage
        result[0]["close"] = 999.0
        assert data_source._historical_bars[0]["close"] == 10.5

    def test_load_historical_bars_filters_by_start_time(self, data_source):
        data_source._historical_bars = [
            make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=200, open_=11.0, high=12.0, low=10.0, close=11.5),
            make_bar(time_val=300, open_=12.0, high=13.0, low=11.0, close=12.5),
        ]
        result = data_source.load_historical_bars(start_time=200)
        assert len(result) == 2
        assert result[0]["time"] == 200
        assert result[1]["time"] == 300

    def test_load_historical_bars_deduplicates(self, data_source):
        data_source._historical_bars = [
            make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=100, open_=10.1, high=11.1, low=9.1, close=10.6),
            make_bar(time_val=200, open_=11.0, high=12.0, low=10.0, close=11.5),
        ]
        result = data_source.load_historical_bars()
        assert len(result) == 2
        assert result[0]["time"] == 100
        assert result[1]["time"] == 200

    def test_load_historical_bars_1m_returns_dicts(self, data_source):
        data_source._historical_bars = [
            make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5),
        ]
        result = data_source.load_historical_bars(timeframe="1m")
        assert isinstance(result, list)
        assert result[0] == data_source._historical_bars[0]

    def test_aggregate_bars_5m(self, data_source):
        bars = [
            make_bar(time_val=0, open_=10.0, high=11.0, low=9.0, close=10.5, volume=100),
            make_bar(time_val=60, open_=10.5, high=12.0, low=10.0, close=11.5, volume=200),
            make_bar(time_val=120, open_=11.5, high=13.0, low=11.0, close=12.5, volume=300),
            make_bar(time_val=300, open_=12.5, high=14.0, low=12.0, close=13.5, volume=400),
        ]
        result = data_source._aggregate_bars(bars, "5m")
        assert len(result) == 2
        # First window: 0-240s
        assert result[0]["time"] == 0
        assert result[0]["open"] == 10.0
        assert result[0]["high"] == 13.0
        assert result[0]["low"] == 9.0
        assert result[0]["close"] == 12.5
        assert result[0]["volume"] == 600
        # Second window: 300s
        assert result[1]["time"] == 300
        assert result[1]["volume"] == 400

    def test_aggregate_bars_1h(self, data_source):
        bars = [
            make_bar(time_val=0, open_=10.0, high=11.0, low=9.0, close=10.5, volume=100),
            make_bar(time_val=1800, open_=10.5, high=12.0, low=10.0, close=11.5, volume=200),
            make_bar(time_val=3600, open_=11.5, high=13.0, low=11.0, close=12.5, volume=300),
        ]
        result = data_source._aggregate_bars(bars, "1h")
        assert len(result) == 2
        assert result[0]["time"] == 0
        assert result[0]["volume"] == 300
        assert result[1]["time"] == 3600

    def test_aggregate_bars_unknown_timeframe_returns_copy(self, data_source):
        bars = [
            make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5),
        ]
        result = data_source._aggregate_bars(bars, "1d")
        assert len(result) == 1
        assert result[0]["time"] == 100

    def test_load_historical_bars_aggregates(self, data_source):
        data_source._historical_bars = [
            make_bar(time_val=0, open_=10.0, high=11.0, low=9.0, close=10.5, volume=100),
            make_bar(time_val=60, open_=10.5, high=12.0, low=10.0, close=11.5, volume=200),
        ]
        result = data_source.load_historical_bars(timeframe="5m")
        assert len(result) == 1
        assert result[0]["volume"] == 300


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


class TestGapDetection:

    def test_detect_gap_logs_warning(self, data_source, logger):
        data_source._detect_gap(0, 200, "TEST")
        # Code path exercised; we just verify no exception is raised.
        assert len(data_source._historical_bars) == 0  # no state change

    def test_detect_gap_no_warning_below_threshold(self, data_source, logger):
        data_source._detect_gap(0, 30, "TEST")
        # Should not log anything for gaps <= 60s

    def test_scan_for_gaps(self, data_source):
        bars = [
            make_bar(time_val=0, open_=10.0, high=11.0, low=9.0, close=10.5),
            make_bar(time_val=90, open_=11.0, high=12.0, low=10.0, close=11.5),
            make_bar(time_val=180, open_=12.0, high=13.0, low=11.0, close=12.5),
        ]
        count = data_source._scan_for_gaps(bars, "TEST")
        assert count == 2

    def test_scan_for_gaps_limits_warnings(self, data_source, logger):
        bars = [
            make_bar(time_val=i * 90, open_=10.0, high=11.0, low=9.0, close=10.5)
            for i in range(10)
        ]
        count = data_source._scan_for_gaps(bars, "TEST")
        assert count == 9

    def test_scan_for_gaps_empty_or_single(self, data_source):
        assert data_source._scan_for_gaps([], "TEST") == 0
        assert data_source._scan_for_gaps([make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)], "TEST") == 0


# ---------------------------------------------------------------------------
# Subscribe / Pause (threading behaviour)
# ---------------------------------------------------------------------------


class TestSubscribePause:

    def test_subscribe_sets_callback_and_blocks_until_pause(self, data_source):
        received = []

        def callback(bar):
            received.append(bar)

        # Start subscribe in a background thread so we don't deadlock the test
        def run_subscribe():
            data_source.subscribe(callback, from_time=50)

        t = threading.Thread(target=run_subscribe, daemon=True)
        t.start()

        # Give the thread time to enter subscribe()
        time.sleep(0.05)
        assert data_source._callback is callback
        assert data_source._from_time == 50
        assert t.is_alive()

        data_source.pause()
        t.join(timeout=1.0)
        assert not t.is_alive()

    def test_pause_sets_stop_event(self, data_source):
        data_source._stop_event.clear()
        data_source.pause()
        assert data_source._stop_event.is_set()

    def test_shutdown_sets_stop_event(self, data_source):
        data_source._stop_event.clear()
        data_source.shutdown()
        assert data_source._stop_event.is_set()


# ---------------------------------------------------------------------------
# Request refresh
# ---------------------------------------------------------------------------


class TestRequestRefresh:

    def test_request_refresh(self, data_source, mock_gateway):
        data_source._state = DataSourceState.CONNECTED
        data_source.request_refresh(days=5)
        mock_gateway.send_refresh_request.assert_called_once_with(days=5)

    def test_request_refresh_ensures_gateway(self, logger):
        ds = ZMQDataSource(logger=logger)
        ds._state = DataSourceState.CONNECTED
        with patch("src.infrastructure.gateway.datasource.TradingGateway") as MockGW:
            mock_gw = MagicMock(spec=TradingGateway)
            MockGW.return_value = mock_gw
            ds.request_refresh(days=3)
            mock_gw.send_refresh_request.assert_called_once_with(days=3)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:

    def test_on_bar_callback_error_propagates(self, data_source):
        # _on_bar does NOT catch on_live_bar exceptions, so they propagate
        data_source.on_live_bar = lambda bar: (_ for _ in ()).throw(RuntimeError("boom"))
        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        with pytest.raises(RuntimeError, match="boom"):
            data_source._on_bar(bar)
        # The bar is still stored before the callback runs
        assert len(data_source._historical_bars) == 1

    def test_on_history_end_callback_error_does_not_abort(self, data_source):
        data_source.on_history_complete = lambda bars: (_ for _ in ()).throw(RuntimeError("boom"))
        data_source._historical_bars = [make_bar(time_val=int(time.time()) - 300, open_=10.0, high=11.0, low=9.0, close=10.5)]
        data_source._on_history_end()
        assert data_source.state == DataSourceState.LIVE

    def test_on_refresh_start_callback_error_does_not_abort(self, data_source):
        data_source.on_before_refresh = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        data_source._on_refresh_start()
        assert data_source.state == DataSourceState.REFRESHING

    def test_on_history_batch_empty_bars(self, data_source):
        payload = {"bars": [], "days": 1}
        data_source._on_history_batch(payload)
        assert len(data_source._historical_bars) == 0
        assert data_source._stats["history_batches"] == 1

    def test_on_tick_missing_volume_defaults_to_zero(self, data_source):
        tick = {"time": 1000, "price": 5000.0}
        data_source._on_tick(tick)
        assert data_source._current_bar["volume"] == 0

    def test_load_historical_bars_thread_safe(self, data_source):
        data_source._historical_bars = [
            make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5),
        ]
        # Replace the lock with a MagicMock that tracks acquire calls
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        data_source._bars_lock = mock_lock
        data_source.load_historical_bars()
        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()


# ---------------------------------------------------------------------------
# Integration-style: end-to-end message flow
# ---------------------------------------------------------------------------


class TestEndToEndFlow:

    def test_full_refresh_cycle(self, data_source, mock_gateway):
        now = int(time.time())
        live_bars = []
        history_complete = []
        data_source.on_live_bar = lambda bar: live_bars.append(bar)
        data_source.on_history_complete = lambda bars: history_complete.append(bars)

        # 1. Refresh starts
        data_source._on_refresh_start()
        assert data_source.state == DataSourceState.REFRESHING

        # 2. History batch arrives
        data_source._on_history_batch({
            "bars": [
                {"time": now - 400, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100},
                {"time": now - 300, "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 200},
            ],
            "days": 1,
        })
        assert len(data_source._historical_bars) == 2

        # 3. A live bar arrives during refresh (should be buffered)
        data_source._on_bar(make_bar(time_val=now - 200, open_=11.5, high=13.0, low=11.0, close=12.5))
        assert len(data_source._historical_bars) == 2  # not yet added
        assert len(data_source._refresh_buffer) == 1

        # 4. History end arrives
        data_source._on_history_end()
        assert data_source.state == DataSourceState.LIVE
        assert len(history_complete) == 1
        assert len(history_complete[0]) == 2
        # Buffered bar should have been flushed
        assert len(data_source._historical_bars) == 3
        assert len(live_bars) == 1  # flushed bar triggers on_live_bar

    def test_gateway_callback_registration(self, data_source, mock_gateway):
        data_source.start()
        callbacks = mock_gateway._callbacks

        # Simulate tick through gateway
        tick = make_tick(time_val=1000, price=5000.0)
        for cb in callbacks[MessageType.TICK]:
            cb(tick)
        assert data_source._stats["ticks_received"] == 1

        # Simulate bar through gateway
        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        for cb in callbacks[MessageType.BAR]:
            cb(bar)
        assert data_source._stats["bars_received"] == 1

        # Simulate history batch through gateway
        fresh_time = int(time.time()) - 300
        for cb in callbacks[MessageType.HISTORY_BATCH]:
            cb({"bars": [{"time": fresh_time, "open": 9.0, "high": 10.0, "low": 8.0, "close": 9.5, "volume": 50}], "days": 1})
        assert data_source._stats["history_batches"] == 1

        # Simulate history end through gateway
        for cb in callbacks[MessageType.HISTORY_END]:
            cb({})
        assert data_source.state == DataSourceState.LIVE

        # Simulate refresh start through gateway
        for cb in callbacks[MessageType.REFRESH_START]:
            cb({})
        assert data_source.state == DataSourceState.REFRESHING


# ---------------------------------------------------------------------------
# Heartbeat monitoring
# ---------------------------------------------------------------------------


class RecordingLogger(FakeLogger):
    """Fake logger that records all messages for test assertions."""

    def __init__(self):
        self.messages = []

    def debug(self, message: str) -> None:
        self.messages.append(message)

    def info(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:
        self.messages.append(message)

    def error(self, message: str) -> None:
        self.messages.append(message)


class TestHeartbeatMonitoring:

    def test_start_heartbeat_monitor_sets_baseline(self, data_source):
        data_source._start_heartbeat_monitor()
        assert data_source._heartbeat_thread is not None
        assert data_source._heartbeat_thread.is_alive()
        assert data_source._heartbeat_alert_sent is False
        assert data_source._last_completed_bar_time > 0
        data_source._stop_heartbeat_monitor()

    def test_stop_heartbeat_monitor_cleans_up(self, data_source):
        data_source._start_heartbeat_monitor()
        data_source._stop_heartbeat_monitor()
        assert data_source._heartbeat_thread is None

    def test_heartbeat_alert_fires_when_bars_stall(self, data_source):
        logger = RecordingLogger()
        data_source.logger = logger
        mock_notifier = MagicMock()
        data_source._notifier = mock_notifier
        data_source._heartbeat_check_interval_sec = 0.01
        data_source._heartbeat_alert_threshold_sec = 0.05
        data_source._state = DataSourceState.LIVE
        data_source._last_completed_bar_time = time.monotonic() - 0.1
        data_source._start_heartbeat_monitor()
        time.sleep(0.15)
        data_source._stop_heartbeat_monitor()
        assert any("🚨 ALERT: No completed bar received" in m for m in logger.messages)
        mock_notifier.send.assert_called_once()

    def test_heartbeat_alert_resets_when_bars_resume(self, data_source):
        logger = RecordingLogger()
        data_source.logger = logger
        data_source._heartbeat_check_interval_sec = 0.01
        data_source._heartbeat_alert_threshold_sec = 0.05
        data_source._state = DataSourceState.LIVE
        data_source._last_completed_bar_time = time.monotonic() - 0.1
        data_source._start_heartbeat_monitor()
        time.sleep(0.15)
        # Now send a bar to resume
        data_source._on_bar(make_bar(time_val=1000, open_=10.0, high=11.0, low=9.0, close=10.5))
        time.sleep(0.05)
        data_source._stop_heartbeat_monitor()
        assert any("Completed-bar stream resumed" in m for m in logger.messages)

    def test_heartbeat_no_alert_when_not_live(self, data_source):
        logger = RecordingLogger()
        data_source.logger = logger
        data_source._heartbeat_check_interval_sec = 0.01
        data_source._heartbeat_alert_threshold_sec = 0.05
        data_source._state = DataSourceState.CONNECTED
        data_source._last_completed_bar_time = time.monotonic() - 0.1
        data_source._start_heartbeat_monitor()
        time.sleep(0.15)
        data_source._stop_heartbeat_monitor()
        assert not any("🚨 ALERT" in m for m in logger.messages)

    def test_on_bar_updates_last_completed_bar_time(self, data_source):
        data_source._state = DataSourceState.LIVE
        before = time.monotonic()
        data_source._on_bar(make_bar(time_val=1000, open_=10.0, high=11.0, low=9.0, close=10.5))
        assert data_source._last_completed_bar_time >= before


# ---------------------------------------------------------------------------
# Market status handling
# ---------------------------------------------------------------------------


class TestMarketStatusHandling:

    def test_on_market_status_updates_flag(self, data_source):
        data_source._on_market_status({"market_open": False, "next_open": 1700000000, "pair": "MNQ"})
        assert data_source._market_is_open is False

    def test_duplicate_bar_suppressed_when_market_closed(self, data_source):
        data_source._market_is_open = False
        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._on_bar(bar)
        data_source._on_bar(bar)
        assert len(data_source._historical_bars) == 1
        assert data_source._duplicate_count == 1

    def test_duplicate_bar_warned_when_market_open(self, data_source):
        logger = RecordingLogger()
        data_source.logger = logger
        data_source._market_is_open = True
        bar = make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5)
        data_source._on_bar(bar)
        data_source._on_bar(bar)
        assert len(data_source._historical_bars) == 1
        assert data_source._duplicate_count == 1
        assert any("Duplicate bar" in m for m in logger.messages)

    def test_bar_arrival_reopens_market(self, data_source):
        logger = RecordingLogger()
        data_source.logger = logger
        data_source._market_is_open = False
        data_source._state = DataSourceState.LIVE
        data_source._on_bar(make_bar(time_val=100, open_=10.0, high=11.0, low=9.0, close=10.5))
        assert data_source._market_is_open is True
        assert any("treating market as OPEN" in m for m in logger.messages)


class TestStaleBarFallback:

    def test_stale_fallback_sets_market_closed(self, data_source):
        data_source._market_is_open = True
        data_source._state = DataSourceState.LIVE
        data_source._heartbeat_check_interval_sec = 0.01
        data_source._start_heartbeat_monitor()
        # Set stale time AFTER starting monitor (which resets baseline)
        data_source._last_completed_bar_time = time.monotonic() - 400
        time.sleep(0.05)
        data_source._stop_heartbeat_monitor()
        assert data_source._market_is_open is False

    def test_stale_fallback_does_not_fire_when_recent_bar(self, data_source):
        data_source._market_is_open = True
        data_source._state = DataSourceState.LIVE
        data_source._last_completed_bar_time = time.monotonic()
        data_source._heartbeat_check_interval_sec = 0.01
        data_source._start_heartbeat_monitor()
        time.sleep(0.05)
        data_source._stop_heartbeat_monitor()
        assert data_source._market_is_open is True

    def test_detect_gap_logs_warning_when_market_open(self, data_source):
        """Gap detection should log a warning when market is open."""
        from unittest.mock import MagicMock
        data_source._market_is_open = True
        data_source._gap_threshold = 60
        mock_logger = MagicMock()
        original_logger = data_source.logger
        data_source.logger = mock_logger
        try:
            data_source._detect_gap(100, 200, "TEST")
        finally:
            data_source.logger = original_logger
        mock_logger.warning.assert_called_once()
        call_args = str(mock_logger.warning.call_args)
        assert "GAP DETECTED [TEST]" in call_args

    def test_detect_gap_silent_when_market_closed(self, data_source):
        """Gap detection should be silent when market is closed."""
        from unittest.mock import MagicMock
        data_source._market_is_open = False
        data_source._gap_threshold = 60
        mock_logger = MagicMock()
        original_logger = data_source.logger
        data_source.logger = mock_logger
        try:
            data_source._detect_gap(100, 200, "TEST")
        finally:
            data_source.logger = original_logger
        mock_logger.warning.assert_not_called()
        assert data_source._gap_count == 1  # still counted internally
