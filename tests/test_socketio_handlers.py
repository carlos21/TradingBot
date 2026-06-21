"""Tests for src/routes/socketio_handlers.py.

Tests Socket.IO event handlers with a mocked SocketIO instance.
"""

from unittest.mock import patch

import pytest

from src.config.models import DEFAULT_HISTORY_DAYS
from src.infrastructure.gateway.datasource import DataSourceState, ZMQDataSource
from src.routes.socketio_handlers import register_socketio_handlers
from tests.fakes import FakeDataSource, FakeLogger


class FakeSocketIO:
    """Records emitted events and registered handlers."""

    def __init__(self):
        self.emitted = []
        self.handlers = {}

    def emit(self, event, *args, **kwargs):
        self.emitted.append((event, args, kwargs))

    def on(self, event):
        def decorator(func):
            self.handlers[event] = func
            return func
        return decorator

    def start_background_task(self, target, *args, **kwargs):
        return target(*args, **kwargs)


class FakeBarsLoader:
    def __init__(self):
        self.streaming = False
        self._from_time = 0
        self.current_tf = "1m"
        self.group_size = 1
        self._seek_calls = []
        self._set_tf_calls = []
        self._start_calls = []
        self._pause_calls = []
        self._step_calls = []
        self._jump_calls = []

    def seek(self, from_time):
        self._seek_calls.append(from_time)
        self._from_time = from_time

    def set_timeframe(self, tf):
        self._set_tf_calls.append(tf)
        self.current_tf = tf
        unit = tf[-1]
        num = int(tf[:-1])
        self.group_size = num if unit == "m" else num * 60

    def start(self, from_time=None, stop_at=None):
        self._start_calls.append((from_time, stop_at))
        self.streaming = True

    def pause(self):
        self._pause_calls.append(True)
        self.streaming = False

    def step(self):
        self._step_calls.append(True)

    def jump_day(self, direction=1, fast=True):
        self._jump_calls.append((direction, fast))
        return self._from_time + direction * 86400


class FakeGateway:
    def __init__(self, running=False, connected=False):
        self.is_running = running
        self.is_connected = connected
        self._conn_listeners = []

    def on_connection_change(self, callback):
        self._conn_listeners.append(callback)


class FakeZMQDataSource(ZMQDataSource):
    """Lightweight ZMQDataSource that skips the heavy __init__."""

    def __init__(self, gateway=None, state=DataSourceState.CONNECTED, cached_bars=None):
        # Do NOT call ZMQDataSource.__init__ to avoid side effects.
        self._gateway = gateway
        self._state = state
        self._refresh_calls = []
        self.pair = "MNQ"
        self.history_days = DEFAULT_HISTORY_DAYS
        self._cached_bars = cached_bars or []

    @property
    def state(self):
        return self._state

    def load_historical_bars(self, timeframe="1m", start_time=None):
        return list(self._cached_bars)

    def request_refresh(self, days=None):
        if self._state == DataSourceState.REFRESHING:
            return
        self._refresh_calls.append(days or self.history_days)


@pytest.fixture
def socketio():
    return FakeSocketIO()


@pytest.fixture
def loader():
    return FakeBarsLoader()


@pytest.fixture
def logger():
    return FakeLogger()


class TestConnectHandler:
    @patch("src.routes.socketio_handlers.emit")
    def test_connect_backtest_mode(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["connect"]
        handler(None)

        mock_emit.assert_called_once()
        event, payload = mock_emit.call_args[0]
        assert event == "stream_status"
        assert payload["playing"] is False
        assert payload["live_mode"] is False
        assert payload["gateway_running"] is False
        assert payload["platform_connected"] is False

    @patch("src.routes.socketio_handlers.emit")
    def test_connect_live_mode_with_gateway(self, mock_emit, socketio, loader, logger):
        gateway = FakeGateway(running=True, connected=True)
        data_source = FakeZMQDataSource(gateway=gateway)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["connect"]
        handler(None)

        assert mock_emit.call_count == 2
        first_event, first_payload = mock_emit.call_args_list[0][0]
        assert first_event == "stream_status"
        assert first_payload["playing"] is False
        assert first_payload["live_mode"] is True
        assert first_payload["gateway_running"] is True
        assert first_payload["platform_connected"] is True
        second_event = mock_emit.call_args_list[1][0][0]
        assert second_event == "platform_connected"

    @patch("src.routes.socketio_handlers.emit")
    def test_connect_does_not_request_refresh(self, mock_emit, socketio, loader, logger):
        # Refreshing on every browser connect duplicates the platform-connect
        # refresh and adds load on NinjaTrader. Explicit request_refresh only.
        gateway = FakeGateway()
        data_source = FakeZMQDataSource(gateway=gateway, state=DataSourceState.CONNECTED)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["connect"]
        handler(None)

        assert data_source._refresh_calls == []

    @patch("src.routes.socketio_handlers.emit")
    def test_connect_emits_history_loaded_when_cached_bars_exist(self, mock_emit, socketio, loader, logger):
        gateway = FakeGateway()
        cached = [{"time": 1, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}]
        data_source = FakeZMQDataSource(gateway=gateway, state=DataSourceState.CONNECTED, cached_bars=cached)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["connect"]
        handler(None)

        emitted_events = [call[0][0] for call in mock_emit.call_args_list]
        assert "history_loaded" in emitted_events
        history_loaded_payload = next(call[0][1] for call in mock_emit.call_args_list if call[0][0] == "history_loaded")
        assert history_loaded_payload["bar_count"] == 1

    @patch("src.routes.socketio_handlers.emit")
    def test_connect_dedupes_history_loaded_on_reconnect(self, mock_emit, socketio, loader, logger):
        gateway = FakeGateway()
        cached = [{"time": 1, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}]
        data_source = FakeZMQDataSource(gateway=gateway, state=DataSourceState.CONNECTED, cached_bars=cached)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["connect"]

        # Simulate three reconnects with the same cached bars.
        handler(None)
        handler(None)
        handler(None)

        history_loaded_calls = [call for call in mock_emit.call_args_list if call[0][0] == "history_loaded"]
        assert len(history_loaded_calls) == 1

    @patch("src.routes.socketio_handlers.emit")
    def test_connect_re_emits_history_loaded_when_bars_change(self, mock_emit, socketio, loader, logger):
        gateway = FakeGateway()
        cached = [{"time": 1, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}]
        data_source = FakeZMQDataSource(gateway=gateway, state=DataSourceState.CONNECTED, cached_bars=cached)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["connect"]
        handler(None)

        # Change the cached bars (e.g. after a refresh).
        data_source._cached_bars = [{"time": 2, "open": 2, "high": 3, "low": 1, "close": 2, "volume": 1, "pair": "MNQ"}]
        handler(None)

        history_loaded_calls = [call for call in mock_emit.call_args_list if call[0][0] == "history_loaded"]
        assert len(history_loaded_calls) == 2


class TestStartStreamHandler:
    @patch("src.routes.socketio_handlers.emit")
    def test_start_stream(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["start_stream"]
        handler({"timeframe": "5m", "fromTime": 1000, "stopAt": 2000})

        assert loader._seek_calls == [1000]
        assert loader._set_tf_calls == ["5m"]
        assert loader._start_calls == [(1000, 2000)]
        mock_emit.assert_called_once_with("stream_status", {"playing": True})

    @patch("src.routes.socketio_handlers.emit")
    def test_start_stream_defaults(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["start_stream"]
        handler({})

        assert loader._seek_calls == [0]
        assert loader._set_tf_calls == ["1m"]
        assert loader._start_calls == [(0, None)]
        mock_emit.assert_called_once_with("stream_status", {"playing": True})


class TestPauseStreamHandler:
    @patch("src.routes.socketio_handlers.emit")
    def test_pause_stream_backtest(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        loader.streaming = True
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["pause_stream"]
        handler()

        assert len(loader._pause_calls) == 1
        mock_emit.assert_called_once_with("stream_status", {"playing": False})

    @patch("src.routes.socketio_handlers.emit")
    def test_pause_stream_live_mode_returns_early(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        loader.streaming = True
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["pause_stream"]
        result = handler()

        assert result is None
        assert len(loader._pause_calls) == 0
        mock_emit.assert_not_called()


class TestStepStreamHandler:
    @patch("src.routes.socketio_handlers.emit")
    def test_step_stream_minutes(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["step_stream"]
        handler({"timeframe": "5m", "fromTime": 1000})

        expected_time = 1000 + 5 * 60
        assert loader._seek_calls == [expected_time]
        assert loader._set_tf_calls == ["5m"]
        assert len(loader._step_calls) == 1
        mock_emit.assert_called_once_with("stream_status", {"playing": True})

    @patch("src.routes.socketio_handlers.emit")
    def test_step_stream_hours(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["step_stream"]
        handler({"timeframe": "1h", "fromTime": 0})

        expected_time = 0 + 1 * 60 * 60
        assert loader._seek_calls == [expected_time]
        assert loader._set_tf_calls == ["1h"]
        assert len(loader._step_calls) == 1

    @patch("src.routes.socketio_handlers.emit")
    def test_step_stream_live_mode_returns_early(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["step_stream"]
        result = handler({"timeframe": "1m", "fromTime": 0})

        assert result is None
        assert len(loader._step_calls) == 0
        mock_emit.assert_not_called()


class TestSeekHandler:
    @patch("src.routes.socketio_handlers.emit")
    def test_seek(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["seek"]
        handler({"fromTime": 5000})

        assert loader._seek_calls == [5000]

    @patch("src.routes.socketio_handlers.emit")
    def test_seek_live_mode_returns_early(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["seek"]
        result = handler({"fromTime": 5000})

        assert result is None
        assert loader._seek_calls == []


class TestSetTimeframeHandler:
    @patch("src.routes.socketio_handlers.emit")
    def test_set_timeframe(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["set_timeframe"]
        handler({"timeframe": "15m", "fromTime": 2000})

        assert loader._seek_calls == [2000]
        assert loader._set_tf_calls == ["15m"]


class TestJumpDayHandler:
    @patch("src.routes.socketio_handlers.emit")
    def test_jump_day(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["jump_day"]
        handler({"direction": 1, "fast": True})

        assert loader._jump_calls == [(1, True)]
        mock_emit.assert_called_once_with("jump_result", {"to": loader._from_time + 86400})

    @patch("src.routes.socketio_handlers.emit")
    def test_jump_day_backward(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["jump_day"]
        handler({"direction": -1, "fast": False})

        assert loader._jump_calls == [(-1, False)]

    @patch("src.routes.socketio_handlers.emit")
    def test_jump_day_live_mode_returns_early(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["jump_day"]
        result = handler({"direction": -1, "fast": False})

        assert result is None
        assert loader._jump_calls == []


class TestConnectionChangeCallback:
    def test_registers_and_fires_connected(self, socketio, loader, logger):
        gateway = FakeGateway()
        data_source = FakeZMQDataSource(gateway=gateway)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )

        assert len(gateway._conn_listeners) == 1
        callback = gateway._conn_listeners[0]

        callback(True)
        assert socketio.emitted == [("platform_connected", (), {})]

    def test_registers_and_fires_disconnected(self, socketio, loader, logger):
        gateway = FakeGateway()
        data_source = FakeZMQDataSource(gateway=gateway)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )

        callback = gateway._conn_listeners[0]
        callback(False)
        assert socketio.emitted == [("platform_disconnected", (), {})]

    def test_no_callback_for_non_zmq_datasource(self, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        assert "connect" in socketio.handlers
        assert "start_stream" in socketio.handlers

    def test_no_callback_when_no_gateway(self, socketio, loader, logger):
        data_source = FakeZMQDataSource(gateway=None)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        assert "connect" in socketio.handlers

    def test_conn_change_emits_connected(self, socketio, loader, logger):
        gateway = FakeGateway()
        data_source = FakeZMQDataSource(gateway=gateway, state=DataSourceState.CONNECTED)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )

        callback = gateway._conn_listeners[0]
        callback(True)
        assert data_source._refresh_calls == []  # socketio_handlers no longer drives refresh
        assert ("platform_connected", (), {}) in socketio.emitted

    def test_conn_change_emits_disconnected(self, socketio, loader, logger):
        gateway = FakeGateway()
        data_source = FakeZMQDataSource(gateway=gateway, state=DataSourceState.CONNECTED)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )

        callback = gateway._conn_listeners[0]
        callback(False)
        assert data_source._refresh_calls == []
        assert ("platform_disconnected", (), {}) in socketio.emitted

    def test_log_forwarding_is_rate_limited(self, socketio, loader, logger):
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=True,
            _logger=logger,
        )

        for i in range(100):
            logger.info(f"flood message {i}")

        system_log_events = [e for e in socketio.emitted if e[0] == "system_log"]
        # Bucket capacity is 40 and rate is 20/sec; 100 instantaneous calls
        # should be capped near the capacity.
        assert len(system_log_events) <= 50

    def test_log_forwarding_extracts_source_from_prefix(self, socketio, loader, logger):
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=True,
            _logger=logger,
        )

        logger.info("[LiveMode] connection established")
        logger.info("plain message without prefix")

        system_log_events = [e for e in socketio.emitted if e[0] == "system_log"]
        sources = [e[1][0]["source"] for e in system_log_events]
        assert "LiveMode" in sources
        assert "server" in sources


class TestTokenBucket:
    def test_non_positive_rate_always_allows(self):
        from src.routes.socketio_handlers import _TokenBucket

        bucket = _TokenBucket(rate=0, capacity=1)
        assert bucket.allow() is True
        assert bucket.allow() is True

        bucket_neg = _TokenBucket(rate=-1, capacity=1)
        assert bucket_neg.allow() is True


class TestPayloadValidation:
    @patch("src.routes.socketio_handlers.emit")
    def test_start_stream_unsupported_timeframe_emits_error(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["start_stream"]
        handler({"timeframe": "1d"})

        error_event = next((call for call in mock_emit.call_args_list if call[0][0] == "error"), None)
        assert error_event is not None
        assert loader._start_calls == []

    @patch("src.routes.socketio_handlers.emit")
    def test_start_stream_non_numeric_from_time_emits_error(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["start_stream"]
        handler({"fromTime": "abc"})

        error_event = next((call for call in mock_emit.call_args_list if call[0][0] == "error"), None)
        assert error_event is not None
        assert loader._start_calls == []


class TestCleanup:
    def test_cleanup_stops_health_thread_and_unwraps_logger(self, socketio, loader, logger):
        cleanup = register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
        )

        assert getattr(logger, "_socketio_log_wrapped", False) is True
        cleanup()
        assert getattr(logger, "_socketio_log_wrapped", False) is False

    def test_double_registration_does_not_double_wrap(self, socketio, loader, logger):
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
        )
        original_info = logger.info
        cleanup = register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
        )
        # The second registration should not replace the already-wrapped method.
        assert logger.info is original_info
        cleanup()


class TestConnectEdgeCases:
    @patch("src.routes.socketio_handlers.emit")
    def test_connect_logs_and_emits_when_cached_load_fails(self, mock_emit, socketio, loader):
        class _BrokenZMQDataSource(FakeZMQDataSource):
            def load_historical_bars(self, timeframe="1m", start_time=None):
                raise RuntimeError("cache unreachable")

        errors: list[str] = []

        class _RecordingLogger(FakeLogger):
            def error(self, message: str) -> None:
                errors.append(message)

        logger = _RecordingLogger()
        data_source = _BrokenZMQDataSource(gateway=FakeGateway())
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["connect"]
        handler(None)

        emitted_events = [call[0][0] for call in mock_emit.call_args_list]
        assert "history_load_failed" in emitted_events
        assert any("cache unreachable" in e for e in errors)
