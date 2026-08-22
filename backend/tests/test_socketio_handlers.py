"""Tests for src/routes/socketio_handlers.py.

Tests Socket.IO event handlers with a mocked SocketIO instance.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.config.models import DEFAULT_HISTORY_HOURS
from src.domain.parity import ParityResult
from src.infrastructure.gateway.datasource import DataSourceState, ZMQDataSource
from src.routes.socketio_handlers import SocketIOLogForwarder, register_socketio_handlers
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

    def start(self, from_time=None, stop_at=None, pace_bps=None):
        self._start_calls.append((from_time, stop_at, pace_bps))
        self.streaming = True

    def pause(self):
        self._pause_calls.append(True)
        self.streaming = False

    def step(self):
        self._step_calls.append(True)

    def jump_day(self, direction=1, fast=True):
        self._jump_calls.append((direction, fast))
        return self._from_time + direction * 86400


class FakeReadinessMonitor:
    def __init__(self, state="CONNECTED", reason="Platform connected", percent=15):
        self._health = {
            "readiness_state": state,
            "readiness_reason": reason,
            "readiness_percent": percent,
        }
        self.history_complete_calls: list[list[dict]] = []

    def on_history_complete(self, bars: list[dict]) -> None:
        self.history_complete_calls.append(list(bars))

    def needs_history_seed(self) -> bool:
        # Mirrors ReadinessMonitor.needs_history_seed: no seed once the
        # monitor is warming or warm.
        return self._health["readiness_state"] not in ("WARMING_UP", "READY", "LIVE")

    def get_health(self):
        return dict(self._health)


class FakeCoordinator:
    def __init__(self):
        self._sessions: dict[str, FakeSession] = {}
        self._join_calls: list[tuple[str, str]] = []
        self._leave_calls: list[tuple[str, str]] = []

    def require_session(self, symbol):
        if not symbol:
            raise ValueError("symbol is required")
        if symbol not in self._sessions:
            self._sessions[symbol] = FakeSession(symbol)
        return self._sessions[symbol]

    def get_session(self, symbol):
        return self._sessions.get(symbol)

    def get_active_sessions(self):
        return list(self._sessions.values())

    def join_instrument(self, symbol: str, sid: str):
        self._join_calls.append((symbol, sid))
        session = self.require_session(symbol)
        session.join_client(sid)
        if not session._started:
            session.start()
        return session

    def leave_instrument(self, symbol: str, sid: str):
        self._leave_calls.append((symbol, sid))

    def route_bar(self, bar):
        pass


class FakeSession:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.instrument = MagicMock()
        self.instrument.symbol = symbol
        self.bars_loader = FakeBarsLoader()
        self.readiness_monitor = FakeReadinessMonitor()
        self._clients: set[str] = set()
        self._started = False

    def join_client(self, sid: str):
        self._clients.add(sid)

    def leave_client(self, sid: str):
        self._clients.discard(sid)

    def start(self):
        self._started = True

    def on_history_loaded(self, bars: list[dict]) -> None:
        self.readiness_monitor.on_history_complete(bars)


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
        self.history_hours = DEFAULT_HISTORY_HOURS
        self._cached_bars = cached_bars or []

    @property
    def state(self):
        return self._state

    def load_historical_bars(self, timeframe="1m", start_time=None, pair=None):
        return list(self._cached_bars)

    def request_refresh(self, days=None, pair=None):
        if self._state == DataSourceState.REFRESHING:
            return
        self._refresh_calls.append(days or self.history_hours)

    def get_health(self):
        return {
            "state": self._state.name,
            "pairs": {},
            "instruments": [],
            "heartbeat_age_sec": None,
            "duplicate_count": 0,
            "gap_count": 0,
            "ticks_received": 0,
            "bars_received": 0,
            "history_batches": 0,
            "platform_connected": self._gateway is not None and getattr(self._gateway, "is_connected", False),
        }


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
    def test_connect_does_not_emit_cached_history(self, mock_emit, socketio, loader, logger):
        # There is no default instrument: cached bars are emitted per
        # instrument on join_instrument, never on a bare connect.
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
        assert "history_loaded" not in emitted_events


class TestJoinInstrumentHistoryLoaded:
    def _register(self, socketio, loader, logger, data_source, coordinator, deduper=None):
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
            coordinator=coordinator,
            history_loaded_deduper=deduper,
        )
        return socketio.handlers["join_instrument"]

    def _join(self, handler, pair="MNQ", sid="sid-1"):
        with patch("src.routes.socketio_handlers.join_room"), \
             patch("src.routes.socketio_handlers.request", new=MagicMock()) as mock_request:
            mock_request.sid = sid
            handler({"pair": pair})

    def test_join_instrument_emits_history_loaded_when_cached_bars_exist(self, socketio, loader, logger):
        cached = [{"time": 1, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}]
        data_source = FakeZMQDataSource(gateway=FakeGateway(), cached_bars=cached)
        handler = self._register(socketio, loader, logger, data_source, FakeCoordinator())

        self._join(handler)

        history_loaded = [e for e in socketio.emitted if e[0] == "history_loaded"]
        assert len(history_loaded) == 1
        assert history_loaded[0][1][0]["bar_count"] == 1
        assert history_loaded[0][2] == {"room": "MNQ"}

    def test_join_instrument_dedupes_history_loaded_on_rejoin(self, socketio, loader, logger):
        from src.utils.history_loaded_deduper import HistoryLoadedDeduper

        cached = [{"time": 1, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}]
        data_source = FakeZMQDataSource(gateway=FakeGateway(), cached_bars=cached)
        handler = self._register(
            socketio, loader, logger, data_source, FakeCoordinator(),
            deduper=HistoryLoadedDeduper(),
        )

        # Simulate three rejoins with the same cached bars.
        self._join(handler)
        self._join(handler)
        self._join(handler)

        history_loaded = [e for e in socketio.emitted if e[0] == "history_loaded"]
        assert len(history_loaded) == 1

    def test_join_instrument_re_emits_history_loaded_when_bars_change(self, socketio, loader, logger):
        from src.utils.history_loaded_deduper import HistoryLoadedDeduper

        cached = [{"time": 1, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"}]
        data_source = FakeZMQDataSource(gateway=FakeGateway(), cached_bars=cached)
        handler = self._register(
            socketio, loader, logger, data_source, FakeCoordinator(),
            deduper=HistoryLoadedDeduper(),
        )

        self._join(handler)

        # Change the cached bars (e.g. after a refresh).
        data_source._cached_bars = [{"time": 2, "open": 2, "high": 3, "low": 1, "close": 2, "volume": 1, "pair": "MNQ"}]
        self._join(handler)

        history_loaded = [e for e in socketio.emitted if e[0] == "history_loaded"]
        assert len(history_loaded) == 2


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
        assert loader._start_calls == [(1000, 2000, None)]
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
        assert loader._start_calls == [(0, None, None)]
        mock_emit.assert_called_once_with("stream_status", {"playing": True})

    @patch("src.routes.socketio_handlers.emit")
    def test_start_stream_pace_bps_passed_through(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["start_stream"]
        handler({"timeframe": "5m", "fromTime": 1000, "stopAt": 2000, "paceBps": 800})

        assert loader._start_calls == [(1000, 2000, 800.0)]


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


class TestCheckParityHandler:
    def _make_parity_service(self, result=None, side_effect=None):
        service = MagicMock()
        if side_effect is not None:
            service.check_parity.side_effect = side_effect
        else:
            service.check_parity.return_value = result or ParityResult(
                checked_at=1710000000,
                bars_checked=100,
                gaps_found=0,
                gaps=[],
                all_good=True,
                summary="All good",
            )
        return service

    @patch("src.routes.socketio_handlers.emit")
    def test_check_parity_no_payload_succeeds(self, mock_emit, socketio, loader, logger):
        parity_service = self._make_parity_service()
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
            parity_service=parity_service,
        )
        handler = socketio.handlers["check_parity"]
        handler()

        parity_service.check_parity.assert_called_once_with(hours_back=5)
        result_call = next(call for call in mock_emit.call_args_list if call[0][0] == "parity_result")
        assert result_call[0][1]["all_good"] is True
        assert result_call[0][1]["summary"] == "All good"

    @patch("src.routes.socketio_handlers.emit")
    def test_check_parity_empty_payload_succeeds(self, mock_emit, socketio, loader, logger):
        parity_service = self._make_parity_service()
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
            parity_service=parity_service,
        )
        handler = socketio.handlers["check_parity"]
        handler({})

        parity_service.check_parity.assert_called_once_with(hours_back=5)
        result_call = next(call for call in mock_emit.call_args_list if call[0][0] == "parity_result")
        assert result_call[0][1]["all_good"] is True

    @patch("src.routes.socketio_handlers.emit")
    def test_check_parity_service_unavailable(self, mock_emit, socketio, loader, logger):
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
            parity_service=None,
        )
        handler = socketio.handlers["check_parity"]
        handler()

        result_call = next(call for call in mock_emit.call_args_list if call[0][0] == "parity_result")
        assert result_call[0][1]["ok"] is False
        assert "Parity service not available" in result_call[0][1]["error"]

    @patch("src.routes.socketio_handlers.emit")
    def test_check_parity_service_raises(self, mock_emit, socketio, loader, logger):
        parity_service = self._make_parity_service(side_effect=RuntimeError("audit failed"))
        data_source = FakeDataSource()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=False,
            _logger=logger,
            parity_service=parity_service,
        )
        handler = socketio.handlers["check_parity"]
        handler()

        result_call = next(call for call in mock_emit.call_args_list if call[0][0] == "parity_result")
        assert result_call[0][1]["ok"] is False
        assert "audit failed" in result_call[0][1]["error"]


class TestHealthEmission:
    def test_emit_health_is_per_session_room(self, socketio, loader, logger):
        gateway = FakeGateway(running=True, connected=True)
        data_source = FakeZMQDataSource(gateway=gateway, state=DataSourceState.STREAMING)
        coordinator = FakeCoordinator()
        coordinator.require_session("MES")
        coordinator._sessions["MES"].readiness_monitor = FakeReadinessMonitor(
            state="LIVE", reason="Live bar stream active", percent=100
        )

        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
            coordinator=coordinator,
        )

        handler = socketio.handlers["request_health"]
        handler()

        health_events = [e for e in socketio.emitted if e[0] == "health_update"]
        # Only the room-scoped snapshot is emitted: the legacy global payload
        # has no readiness_state and would briefly render the panel NOT READY.
        assert len(health_events) == 1

        room_event = health_events[0]
        assert room_event[2].get("room") == "MES"
        assert room_event[1][0]["readiness_state"] == "LIVE"
        assert room_event[1][0]["pair"] == "MES"

    def test_emit_health_falls_back_to_global_when_no_active_sessions(self, socketio, loader, logger):
        gateway = FakeGateway(running=True, connected=True)
        data_source = FakeZMQDataSource(gateway=gateway, state=DataSourceState.STREAMING)
        coordinator = FakeCoordinator()

        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
            coordinator=coordinator,
        )

        handler = socketio.handlers["request_health"]
        handler()

        health_events = [e for e in socketio.emitted if e[0] == "health_update"]
        assert len(health_events) == 1
        global_event = health_events[0]
        assert "room" not in global_event[2]
        assert global_event[1][0]["state"] == "STREAMING"


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
        events = [e[0] for e in socketio.emitted]
        assert "platform_connected" in events
        assert "health_update" in events

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
        events = [e[0] for e in socketio.emitted]
        assert "platform_disconnected" in events
        assert "health_update" in events

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
        _, logger = register_socketio_handlers(
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
        _, logger = register_socketio_handlers(
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
        cleanup, logger = register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
        )

        assert isinstance(logger, SocketIOLogForwarder) is True
        restored = cleanup()
        assert isinstance(restored, SocketIOLogForwarder) is False

    def test_double_registration_does_not_double_wrap(self, socketio, loader, logger):
        _, wrapped = register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
        )
        cleanup, rewrapped = register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=wrapped,
        )
        # The second registration must not wrap the forwarder again.
        assert rewrapped is wrapped
        cleanup()


class TestJoinInstrumentEdgeCases:
    @patch("src.routes.socketio_handlers.emit")
    def test_join_instrument_logs_and_emits_when_cached_load_fails(self, mock_emit, socketio, loader):
        class _BrokenZMQDataSource(FakeZMQDataSource):
            def load_historical_bars(self, timeframe="1m", start_time=None, pair=None):
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
            coordinator=FakeCoordinator(),
        )
        handler = socketio.handlers["join_instrument"]
        with patch("src.routes.socketio_handlers.join_room"), \
             patch("src.routes.socketio_handlers.request", new=MagicMock()) as mock_request:
            mock_request.sid = "sid-1"
            handler({"pair": "MNQ"})

        emitted_events = [call[0][0] for call in mock_emit.call_args_list]
        assert "history_load_failed" in emitted_events
        assert any("cache unreachable" in e for e in errors)


class TestJoinLeaveInstrument:
    def test_join_instrument_creates_session(self, socketio, loader, logger):
        from unittest.mock import patch
        coordinator = FakeCoordinator()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["join_instrument"]
        with patch("src.routes.socketio_handlers.join_room") as mock_join_room, \
             patch("src.routes.socketio_handlers.request", new=MagicMock()) as mock_request:
            mock_request.sid = "sid-1"
            handler({"pair": "ES"})

        assert coordinator._join_calls == [("ES", "sid-1")]
        assert "ES" in coordinator._sessions
        assert coordinator._sessions["ES"]._started is True

    def test_leave_instrument_removes_client(self, socketio, loader, logger):
        from unittest.mock import patch
        coordinator = FakeCoordinator()
        coordinator.join_instrument("ES", "sid-1")
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["leave_instrument"]
        with patch("src.routes.socketio_handlers.leave_room") as mock_leave_room, \
             patch("src.routes.socketio_handlers.request", new=MagicMock()) as mock_request:
            mock_request.sid = "sid-1"
            handler({"pair": "ES"})

        assert coordinator._leave_calls == [("ES", "sid-1")]

    @patch("src.routes.socketio_handlers.join_room")
    @patch("src.routes.socketio_handlers.request", new=MagicMock())
    def test_join_instrument_seeds_readiness_with_cached_bars_in_live_mode(
        self, mock_join_room, socketio, loader, logger
    ):
        from unittest.mock import patch
        coordinator = FakeCoordinator()
        cached = [
            {"time": 1, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"},
            {"time": 2, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"},
        ]
        data_source = FakeZMQDataSource(gateway=FakeGateway(), cached_bars=cached)
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["join_instrument"]
        with patch("src.routes.socketio_handlers.request", new=MagicMock()) as mock_request:
            mock_request.sid = "sid-1"
            handler({"pair": "MNQ"})

        session = coordinator.get_session("MNQ")
        assert session is not None
        assert len(session.readiness_monitor.history_complete_calls) == 1
        assert session.readiness_monitor.history_complete_calls[0] == cached

    @patch("src.routes.socketio_handlers.join_room")
    def test_join_instrument_does_not_reseed_warm_monitor_in_live_mode(
        self, mock_join_room, socketio, loader, logger
    ):
        from unittest.mock import patch
        coordinator = FakeCoordinator()
        cached = [
            {"time": 1, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"},
            {"time": 2, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "pair": "MNQ"},
        ]
        data_source = FakeZMQDataSource(gateway=FakeGateway(), cached_bars=cached)
        # Simulate a session whose monitor already finished warmup: a browser
        # page refresh / reconnect must not restart the warmup replay.
        coordinator.require_session("MNQ")
        coordinator._sessions["MNQ"].readiness_monitor = FakeReadinessMonitor(
            state="LIVE", reason="Live bar stream active", percent=100
        )
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=data_source,
            live_mode=True,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["join_instrument"]
        with patch("src.routes.socketio_handlers.request", new=MagicMock()) as mock_request:
            mock_request.sid = "sid-1"
            handler({"pair": "MNQ"})

        session = coordinator.get_session("MNQ")
        assert session is not None
        assert session.readiness_monitor.history_complete_calls == []


class TestPerInstrumentCommands:
    @patch("src.routes.socketio_handlers.emit")
    def test_start_stream_targets_session(self, mock_emit, socketio, loader, logger):
        coordinator = FakeCoordinator()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["start_stream"]
        handler({"pair": "ES", "timeframe": "5m", "fromTime": 1000, "stopAt": 2000})

        session = coordinator.require_session("ES")
        assert session.bars_loader._seek_calls == [1000]
        assert session.bars_loader._set_tf_calls == ["5m"]
        assert session.bars_loader._start_calls == [(1000, 2000, None)]

    @patch("src.routes.socketio_handlers.emit")
    def test_pause_stream_targets_session(self, mock_emit, socketio, loader, logger):
        coordinator = FakeCoordinator()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["pause_stream"]
        handler({"pair": "ES"})

        session = coordinator.require_session("ES")
        assert len(session.bars_loader._pause_calls) == 1

    @patch("src.routes.socketio_handlers.emit")
    def test_step_stream_targets_session(self, mock_emit, socketio, loader, logger):
        coordinator = FakeCoordinator()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["step_stream"]
        handler({"pair": "ES", "timeframe": "5m", "fromTime": 1000})

        session = coordinator.require_session("ES")
        expected_time = 1000 + 5 * 60
        assert session.bars_loader._seek_calls == [expected_time]
        assert session.bars_loader._set_tf_calls == ["5m"]
        assert len(session.bars_loader._step_calls) == 1

    @patch("src.routes.socketio_handlers.emit")
    def test_seek_targets_session(self, mock_emit, socketio, loader, logger):
        coordinator = FakeCoordinator()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["seek"]
        handler({"pair": "ES", "fromTime": 5000})

        session = coordinator.require_session("ES")
        assert session.bars_loader._seek_calls == [5000]

    @patch("src.routes.socketio_handlers.emit")
    def test_set_timeframe_targets_session(self, mock_emit, socketio, loader, logger):
        coordinator = FakeCoordinator()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["set_timeframe"]
        handler({"pair": "ES", "timeframe": "15m", "fromTime": 2000})

        session = coordinator.require_session("ES")
        assert session.bars_loader._seek_calls == [2000]
        assert session.bars_loader._set_tf_calls == ["15m"]

    @patch("src.routes.socketio_handlers.emit")
    def test_jump_day_targets_session(self, mock_emit, socketio, loader, logger):
        coordinator = FakeCoordinator()
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
            coordinator=coordinator,
        )
        handler = socketio.handlers["jump_day"]
        handler({"pair": "ES", "direction": 1, "fast": True})

        session = coordinator.require_session("ES")
        assert session.bars_loader._jump_calls == [(1, True)]


class TestBackwardCompatWithoutCoordinator:
    @patch("src.routes.socketio_handlers.emit")
    def test_start_stream_without_pair_uses_default_loader(self, mock_emit, socketio, loader, logger):
        register_socketio_handlers(
            socketio=socketio,
            loader=loader,
            data_source=FakeDataSource(),
            live_mode=False,
            _logger=logger,
        )
        handler = socketio.handlers["start_stream"]
        handler({"timeframe": "5m", "fromTime": 1000})

        assert loader._seek_calls == [1000]
        assert loader._set_tf_calls == ["5m"]
        assert loader._start_calls == [(1000, None, None)]
