"""Tests for src/routes/stream_routes.py."""

import pytest
from flask import Flask

from src.infrastructure.gateway.datasource import DataSourceState, ZMQDataSource
from src.routes.stream_routes import register_stream_routes
from tests.fakes import FakeLogger


class FakeGateway:
    def __init__(self, running=False, connected=False):
        self.is_running = running
        self.is_connected = connected
        self.platform_info = {"platform": "ninjatrader"}


class FakeZMQDataSource(ZMQDataSource):
    def __init__(self, running=False, connected=False):
        # Do NOT call ZMQDataSource.__init__ to avoid heavy side effects.
        self._gateway = FakeGateway(running=running, connected=connected)
        self._started = False
        self._stopped = False
        self.start_calls = 0
        self.stop_calls = 0
        self._state = DataSourceState.DISCONNECTED
        self.refresh_calls = 0
        self.stopped_instruments = []

    def start(self):
        self._started = True
        self.start_calls += 1
        self._gateway.is_running = True

    def stop(self):
        self._stopped = True
        self.stop_calls += 1
        self._gateway.is_running = False
        self._gateway.is_connected = False

    def request_refresh(self, days=None):
        self.refresh_calls += 1

    def stop_instrument_streaming(self, pair):
        self.stopped_instruments.append(pair)


class FakeCoordinator:
    """Minimal coordinator fake tracking sessions by symbol."""

    def __init__(self, symbols=None):
        self._symbols = list(symbols or [])
        self.stopped_sessions = []
        self.stop_all_calls = 0

    def stop_session(self, symbol):
        if symbol not in self._symbols:
            return False
        self._symbols.remove(symbol)
        self.stopped_sessions.append(symbol)
        return True

    def stop_all(self):
        self.stop_all_calls += 1
        self._symbols.clear()

    def list_active_symbols(self):
        return list(self._symbols)


class FakeNonZMQDataSource:
    """A non-ZMQ data source to test error paths."""


class FakePlatformLifecycleService:
    """Satisfies PlatformLifecycleService Protocol for tests."""

    def __init__(self, validation_result=(True, None), has_accounts=True):
        self._validation_result = validation_result
        self._has_accounts = has_accounts
        self.launch_calls = []

    def validate_before_start(self, data_source):
        return self._validation_result

    def maybe_launch_after_delay(self, data_source, trading_mode=None):
        self.launch_calls.append((data_source, trading_mode))

    def has_accounts_configured(self):
        return self._has_accounts


class FakeSocketIO:
    def __init__(self):
        self.emitted = []

    def emit(self, event, *args, **kwargs):
        self.emitted.append((event, args, kwargs))


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def make_registered_app(
    app,
    data_source=None,
    platform_lifecycle=None,
    socketio=None,
    logger=None,
    coordinator=None,
):
    register_stream_routes(
        app=app,
        data_source=data_source or FakeZMQDataSource(),
        platform_lifecycle=platform_lifecycle or FakePlatformLifecycleService(),
        socketio=socketio or FakeSocketIO(),
        logger=logger or FakeLogger(),
        coordinator=coordinator,
    )
    return app


class TestStreamStatus:

    def test_status_not_live_mode(self, app):
        lifecycle = FakePlatformLifecycleService(has_accounts=True)
        make_registered_app(app, data_source=FakeNonZMQDataSource(), platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.get("/api/stream/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["live_mode"] is False
            assert data["gateway_running"] is False
            assert data["platform_connected"] is False
            assert data["platform_info"] is None

    def test_status_live_mode_disconnected(self, app):
        lifecycle = FakePlatformLifecycleService(has_accounts=False)
        make_registered_app(app, data_source=FakeZMQDataSource(running=False, connected=False), platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.get("/api/stream/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["live_mode"] is True
            assert data["gateway_running"] is False
            assert data["platform_connected"] is False
            assert data["has_accounts"] is False

    def test_status_live_mode_connected(self, app):
        lifecycle = FakePlatformLifecycleService(has_accounts=True)
        make_registered_app(app, data_source=FakeZMQDataSource(running=True, connected=True), platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.get("/api/stream/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["live_mode"] is True
            assert data["gateway_running"] is True
            assert data["platform_connected"] is True
            assert data["platform_info"] == {"platform": "ninjatrader"}
            assert data["has_accounts"] is True

    def test_status_no_gateway(self, app):
        ds = FakeZMQDataSource()
        ds._gateway = None
        lifecycle = FakePlatformLifecycleService(has_accounts=True)
        make_registered_app(app, data_source=ds, platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.get("/api/stream/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["gateway_running"] is False
            assert data["platform_connected"] is False
            assert data["platform_info"] is None


class TestStreamStart:

    def test_start_not_live_mode(self, app):
        make_registered_app(app, data_source=FakeNonZMQDataSource())
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["status"] == "error"
            assert "ZMQ" in data["message"]

    def test_start_validation_fails(self, app):
        lifecycle = FakePlatformLifecycleService(validation_result=(False, "No accounts configured"))
        make_registered_app(app, data_source=FakeZMQDataSource(running=False, connected=False), platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["status"] == "error"
            assert "No accounts configured" in data["message"]

    def test_start_already_connected(self, app):
        ds = FakeZMQDataSource(running=True, connected=True)
        make_registered_app(app, data_source=ds)
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "already_connected"
            # A connected-but-not-streaming datasource gets a recovery kick.
            assert ds.refresh_calls == 1

    def test_start_already_connected_streaming_does_not_refresh(self, app):
        ds = FakeZMQDataSource(running=True, connected=True)
        ds._state = DataSourceState.STREAMING
        make_registered_app(app, data_source=ds)
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "already_connected"
            # A healthy stream must not be disturbed by a redundant refresh.
            assert ds.refresh_calls == 0

    def test_start_starts_gateway(self, app):
        ds = FakeZMQDataSource(running=False, connected=False)
        socketio = FakeSocketIO()
        lifecycle = FakePlatformLifecycleService()
        make_registered_app(app, data_source=ds, socketio=socketio, platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "starting"
            assert ds._started is True
            assert ("gateway_started", (), {}) in socketio.emitted
            # Background thread should have been spawned
            assert len(lifecycle.launch_calls) == 1

    def test_start_forwards_trading_mode(self, app):
        ds = FakeZMQDataSource(running=True, connected=False)
        lifecycle = FakePlatformLifecycleService()
        make_registered_app(app, data_source=ds, platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.post("/api/stream/start", json={"trading_mode": "simulation"})
            assert resp.status_code == 200
            assert len(lifecycle.launch_calls) == 1
            assert lifecycle.launch_calls[0][1] == "simulation"

    def test_start_without_trading_mode_passes_none(self, app):
        ds = FakeZMQDataSource(running=True, connected=False)
        lifecycle = FakePlatformLifecycleService()
        make_registered_app(app, data_source=ds, platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            assert len(lifecycle.launch_calls) == 1
            assert lifecycle.launch_calls[0][1] is None

    def test_start_invalid_trading_mode_passes_none(self, app):
        ds = FakeZMQDataSource(running=True, connected=False)
        lifecycle = FakePlatformLifecycleService()
        make_registered_app(app, data_source=ds, platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.post("/api/stream/start", json={"trading_mode": "bogus"})
            assert resp.status_code == 200
            assert len(lifecycle.launch_calls) == 1
            assert lifecycle.launch_calls[0][1] is None

    def test_start_gateway_running_not_connected(self, app):
        ds = FakeZMQDataSource(running=True, connected=False)
        socketio = FakeSocketIO()
        lifecycle = FakePlatformLifecycleService()
        make_registered_app(app, data_source=ds, socketio=socketio, platform_lifecycle=lifecycle)
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "starting"
            # Should not call start() since already running
            assert ds._started is False
            # Background thread should still be spawned
            assert len(lifecycle.launch_calls) == 1

    def test_start_start_failure(self, app):
        class BrokenZMQDataSource(FakeZMQDataSource):
            def start(self):
                raise RuntimeError("bind failed")

        ds = BrokenZMQDataSource(running=False, connected=False)
        make_registered_app(app, data_source=ds)
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["status"] == "error"
            assert "bind failed" in data["message"]

    def test_start_stop_start_cycle_restarts_gateway(self, app):
        """After a stop, a second start must call data_source.start() again."""
        ds = FakeZMQDataSource(running=False, connected=False)
        socketio = FakeSocketIO()
        lifecycle = FakePlatformLifecycleService()
        make_registered_app(app, data_source=ds, socketio=socketio, platform_lifecycle=lifecycle)
        with app.test_client() as client:
            # First start: gateway comes up
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "starting"
            assert ds._gateway.is_running is True

            # Stop: gateway goes down
            resp = client.post("/api/stream/stop")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "stopped"
            assert ds._gateway.is_running is False

            # Second start: must restart the gateway, not skip as already running
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "starting"
            assert ds._gateway.is_running is True
            assert ds.start_calls == 2


class TestStreamStop:

    def test_stop_not_live_mode(self, app):
        make_registered_app(app, data_source=FakeNonZMQDataSource())
        with app.test_client() as client:
            resp = client.post("/api/stream/stop")
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["status"] == "error"

    def test_stop_success(self, app):
        ds = FakeZMQDataSource(running=True, connected=True)
        socketio = FakeSocketIO()
        make_registered_app(app, data_source=ds, socketio=socketio)
        with app.test_client() as client:
            resp = client.post("/api/stream/stop")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "stopped"
            assert ds._stopped is True
            assert ("gateway_stopped", (), {}) in socketio.emitted

    def test_stop_failure(self, app):
        class BrokenZMQDataSource(FakeZMQDataSource):
            def stop(self):
                raise RuntimeError("stop failed")

        ds = BrokenZMQDataSource(running=True, connected=True)
        make_registered_app(app, data_source=ds)
        with app.test_client() as client:
            resp = client.post("/api/stream/stop")
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["status"] == "error"
            assert "stop failed" in data["message"]


class TestStreamStopSingleInstrument:
    """Per-instrument stop: POST /api/stream/stop with a JSON {"pair": ...}."""

    def test_stop_with_pair_keeps_gateway_alive_while_others_stream(self, app):
        ds = FakeZMQDataSource(running=True, connected=True)
        socketio = FakeSocketIO()
        coordinator = FakeCoordinator(symbols=["MNQ", "MES"])
        make_registered_app(app, data_source=ds, socketio=socketio, coordinator=coordinator)
        with app.test_client() as client:
            resp = client.post("/api/stream/stop", json={"pair": "MES"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "stopped"
            # Only the MES session was stopped; MNQ keeps streaming and the
            # gateway stays up.
            assert coordinator.stopped_sessions == ["MES"]
            assert coordinator.list_active_symbols() == ["MNQ"]
            assert ds.stopped_instruments == ["MES"]
            assert ds.stop_calls == 0
            assert ds._gateway.is_running is True
            # A pair-scoped event went out; no global gateway_stopped.
            assert ("stream_stopped", ({"pair": "MES"},), {}) in socketio.emitted
            assert not any(e[0] == "gateway_stopped" for e in socketio.emitted)

    def test_stop_with_last_pair_triggers_full_gateway_stop(self, app):
        ds = FakeZMQDataSource(running=True, connected=True)
        socketio = FakeSocketIO()
        coordinator = FakeCoordinator(symbols=["MES"])
        make_registered_app(app, data_source=ds, socketio=socketio, coordinator=coordinator)
        with app.test_client() as client:
            resp = client.post("/api/stream/stop", json={"pair": "MES"})
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "stopped"
            # Last instrument stopped: the whole gateway goes down too.
            assert ds.stopped_instruments == ["MES"]
            assert ds.stop_calls == 1
            assert ("stream_stopped", ({"pair": "MES"},), {}) in socketio.emitted
            assert ("gateway_stopped", (), {}) in socketio.emitted

    def test_stop_with_pair_and_no_coordinator_falls_back_to_full_stop(self, app):
        ds = FakeZMQDataSource(running=True, connected=True)
        socketio = FakeSocketIO()
        make_registered_app(app, data_source=ds, socketio=socketio, coordinator=None)
        with app.test_client() as client:
            resp = client.post("/api/stream/stop", json={"pair": "MES"})
            assert resp.status_code == 200
            # With no coordinator nothing can be active afterwards, so the
            # gateway is stopped like a single-instrument stop.
            assert ds.stopped_instruments == ["MES"]
            assert ds.stop_calls == 1
            assert ("stream_stopped", ({"pair": "MES"},), {}) in socketio.emitted
            assert ("gateway_stopped", (), {}) in socketio.emitted

    def test_stop_without_pair_is_legacy_global_stop(self, app):
        ds = FakeZMQDataSource(running=True, connected=True)
        socketio = FakeSocketIO()
        coordinator = FakeCoordinator(symbols=["MNQ", "MES"])
        make_registered_app(app, data_source=ds, socketio=socketio, coordinator=coordinator)
        with app.test_client() as client:
            resp = client.post("/api/stream/stop")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "stopped"
            assert ds.stop_calls == 1
            assert coordinator.stop_all_calls == 1
            assert coordinator.stopped_sessions == []
            assert ds.stopped_instruments == []
            assert ("gateway_stopped", (), {}) in socketio.emitted

    def test_stop_with_pair_failure_returns_500(self, app):
        class BrokenZMQDataSource(FakeZMQDataSource):
            def stop_instrument_streaming(self, pair):
                raise RuntimeError("unsubscribe failed")

        ds = BrokenZMQDataSource(running=True, connected=True)
        coordinator = FakeCoordinator(symbols=["MNQ", "MES"])
        make_registered_app(app, data_source=ds, coordinator=coordinator)
        with app.test_client() as client:
            resp = client.post("/api/stream/stop", json={"pair": "MES"})
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["status"] == "error"
            assert "unsubscribe failed" in data["message"]
