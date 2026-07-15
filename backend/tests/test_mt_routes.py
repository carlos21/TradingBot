"""Tests for src/routes/mt_routes.py."""

from unittest.mock import MagicMock

import pytest
from flask import Flask

from src.routes.mt_routes import register_mt_routes
from tests.fakes import FakeLogger


class RecordingLogger(FakeLogger):
    """FakeLogger that records error calls for assertions."""

    def __init__(self):
        self.errors = []

    def error(self, message: str) -> None:
        self.errors.append(message)


class FakeMtService:
    def __init__(self):
        self.last_exe_path = "UNCALLED"

    def launch_terminal(self, exe_path=None):
        self.last_exe_path = exe_path
        return {"success": True, "message": f"launched {exe_path}"}


class FakeDeployService:
    def __init__(self):
        self.last_call = {}

    def deploy_metatrader(self, target_dir=None, pair="NAS100", ports=None):
        self.last_call = {"target_dir": target_dir, "pair": pair, "ports": ports}
        return {"success": True, "message": "deployed", "copied": []}


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    mt = FakeMtService()
    deploy = FakeDeployService()
    logger = RecordingLogger()
    register_mt_routes(app, mt, deploy, logger)
    app.config["mt_service"] = mt
    app.config["deploy_service"] = deploy
    app.config["logger"] = logger
    return app


class TestMtRoutes:
    def test_launch_no_payload(self, app):
        with app.test_client() as client:
            resp = client.post("/api/mt/launch")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert app.config["mt_service"].last_exe_path is None

    def test_launch_with_exe_path(self, app):
        with app.test_client() as client:
            resp = client.post("/api/mt/launch", json={"exe_path": "C:\\\\MetaTrader 5\\\\terminal64.exe"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert app.config["mt_service"].last_exe_path == "C:\\\\MetaTrader 5\\\\terminal64.exe"

    def test_launch_exception_returns_500(self, app):
        app.config["mt_service"].launch_terminal = MagicMock(side_effect=Exception("boom"))
        with app.test_client() as client:
            resp = client.post("/api/mt/launch")
            assert resp.status_code == 500
            data = resp.get_json()
            assert "Internal server error" in data["message"]
            assert len(app.config["logger"].errors) == 1

    def test_deploy_no_payload(self, app):
        with app.test_client() as client:
            resp = client.post("/api/mt/deploy")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert app.config["deploy_service"].last_call["target_dir"] is None
            assert app.config["deploy_service"].last_call["pair"] == "NAS100"

    def test_deploy_with_options(self, app):
        ports = {"market_port": 6001, "command_port": 6002}
        with app.test_client() as client:
            resp = client.post(
                "/api/mt/deploy",
                json={"target_dir": "/some/mql5", "pair": "EURUSD", "ports": ports},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert app.config["deploy_service"].last_call["target_dir"] == "/some/mql5"
            assert app.config["deploy_service"].last_call["pair"] == "EURUSD"
            assert app.config["deploy_service"].last_call["ports"] == ports

    def test_deploy_exception_returns_500(self, app):
        app.config["deploy_service"].deploy_metatrader = MagicMock(side_effect=Exception("boom"))
        with app.test_client() as client:
            resp = client.post("/api/mt/deploy")
            assert resp.status_code == 500
            data = resp.get_json()
            assert "Internal server error" in data["message"]
            assert len(app.config["logger"].errors) == 1
