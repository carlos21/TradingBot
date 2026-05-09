"""Tests for src/routes/socketio_handlers.py.

Tests Socket.IO event handlers with a mocked SocketIO instance.
"""

from unittest.mock import patch

import pytest

from src.infrastructure.gateway.datasource import ZMQDataSource
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

    def __init__(self, gateway=None, refreshing=False):
        # Do NOT call ZMQDataSource.__init__ to avoid side effects.
        self._gateway = gateway
        self._refreshing = refreshing
        self._refresh_calls = []
        self.pair = "MNQ"

    @property
    def is_refreshing(self):
        return self._refreshing

    def request_refresh(self, days=1):
        self._refresh_calls.append(days)


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
            nt_account_names=[],
        )
        handler = socketio.handlers["connect"]
        handler(None)

        mock_emit.assert_called_once()
        event, payload = mock_emit.call_args[0]
        assert event == "stream_status"
        assert payload["playing"] is False
        assert payload["live_mode"] is False
        assert payload["nt_accounts"] == []
        assert payload["gateway_running"] is False
        assert payload["platform_connected"] is False
        assert payload["streaming_disabled_reason"] is None

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
            nt_account_names=["Sim101"],
        )
        handler = socketio.handlers["connect"]
        handler(None)

        mock_emit.assert_called_once()
        event, payload = mock_emit.call_args[0]
        assert event == "stream_status"
        assert payload["playing"] is False
        assert payload["live_mode"] is True
        assert payload["nt_accounts"] == ["Sim101"]
        assert payload["gateway_running"] is True
        assert payload["platform_connected"] is True
        assert payload["streaming_disabled_reason"] is None

    @patch("src.routes.socketio_handlers.emit")
    def test_connect_live_mode_no_accounts_shows_disabled_reason(self, mock_emit, socketio, loader, logger):
        data_source = FakeZMQDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
            nt_account_names=[],
        )
        handler = socketio.handlers["connect"]
        handler(None)

        mock_emit.assert_called_once()
        event, payload = mock_emit.call_args[0]
        assert payload["streaming_disabled_reason"] == "No NT accounts configured"

    @patch("src.routes.socketio_handlers.emit")
    def test_connect_requests_refresh_when_not_refreshing(self, mock_emit, socketio, loader, logger):
        gateway = FakeGateway()
        data_source = FakeZMQDataSource(gateway=gateway, refreshing=False)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
        )
        handler = socketio.handlers["connect"]
        handler(None)

        assert data_source._refresh_calls == [1]

    @patch("src.routes.socketio_handlers.emit")
    def test_connect_skips_refresh_when_already_refreshing(self, mock_emit, socketio, loader, logger):
        gateway = FakeGateway()
        data_source = FakeZMQDataSource(gateway=gateway, refreshing=True)
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
