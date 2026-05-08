"""Tests for src/routes/stream_routes.py."""

import pytest
from flask import Flask

from src.gateway.datasource import ZMQDataSource
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

    def start(self):
        self._started = True
        self._gateway.is_running = True

    def stop(self):
        self._stopped = True
        self._gateway.is_running = False
        self._gateway.is_connected = False


class FakeNonZMQDataSource:
    """A non-ZMQ data source to test error paths."""
    pass


class FakeNtService:
    def __init__(self, success=True):
        self._success = success
        self.calls = []

    def open_nt_and_login(self, username, password):
        self.calls.append((username, password))
        return {"success": self._success, "message": "ok" if self._success else "failed"}


class FakeSettingsService:
    def __init__(self, settings=None):
        self._settings = settings or {}

    def get_full_settings(self):
        return self._settings


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
    nt_service=None,
    settings_service=None,
    socketio=None,
    logger=None,
):
    register_stream_routes(
        app=app,
        data_source=data_source or FakeZMQDataSource(),
        nt_service=nt_service or FakeNtService(),
        settings_service=settings_service or FakeSettingsService(),
        socketio=socketio or FakeSocketIO(),
        logger=logger or FakeLogger(),
    )
    return app


class TestStreamStatus:

    def test_status_not_live_mode(self, app):
        make_registered_app(app, data_source=FakeNonZMQDataSource())
        with app.test_client() as client:
            resp = client.get("/api/stream/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["live_mode"] is False
            assert data["gateway_running"] is False
            assert data["platform_connected"] is False
            assert data["platform_info"] is None

    def test_status_live_mode_disconnected(self, app):
        make_registered_app(app, data_source=FakeZMQDataSource(running=False, connected=False))
        with app.test_client() as client:
            resp = client.get("/api/stream/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["live_mode"] is True
            assert data["gateway_running"] is False
            assert data["platform_connected"] is False

    def test_status_live_mode_connected(self, app):
        make_registered_app(app, data_source=FakeZMQDataSource(running=True, connected=True))
        with app.test_client() as client:
            resp = client.get("/api/stream/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["live_mode"] is True
            assert data["gateway_running"] is True
            assert data["platform_connected"] is True
            assert data["platform_info"] == {"platform": "ninjatrader"}

    def test_status_no_gateway(self, app):
        ds = FakeZMQDataSource()
        ds._gateway = None
        make_registered_app(app, data_source=ds)
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

    def test_start_already_connected(self, app):
        make_registered_app(app, data_source=FakeZMQDataSource(running=True, connected=True))
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "already_connected"

    def test_start_starts_gateway(self, app):
        ds = FakeZMQDataSource(running=False, connected=False)
        socketio = FakeSocketIO()
        make_registered_app(app, data_source=ds, socketio=socketio)
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "starting"
            assert ds._started is True
            assert ("gateway_started", (), {}) in socketio.emitted

    def test_start_gateway_running_not_connected(self, app):
        ds = FakeZMQDataSource(running=True, connected=False)
        socketio = FakeSocketIO()
        make_registered_app(app, data_source=ds, socketio=socketio)
        with app.test_client() as client:
            resp = client.post("/api/stream/start")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "starting"
            # Should not call start() since already running
            assert ds._started is False

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
