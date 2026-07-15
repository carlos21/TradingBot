"""Tests for src/routes/nt_routes.py."""

from unittest.mock import MagicMock

import pytest
from flask import Flask

from src.routes.nt_routes import register_nt_routes
from tests.fakes import FakeLogger


class RecordingLogger(FakeLogger):
    """FakeLogger that records error calls for assertions."""

    def __init__(self):
        self.errors = []

    def error(self, message: str) -> None:
        self.errors.append(message)


class FakeNtService:
    def __init__(self):
        self.install_called = False
        self.open_called = False
        self.last_username = None
        self.last_password = None

    def install_netmq(self):
        self.install_called = True
        return {"success": True}

    def open_nt_and_login(self, username, password):
        self.open_called = True
        self.last_username = username
        self.last_password = password
        return {"success": True, "message": f"Launched for {username}"}


class FakeDeployService:
    def __init__(self):
        self.last_target_dir = "UNCALLED"

    def deploy_ninjatrader(self, target_dir=None):
        self.last_target_dir = target_dir
        return {"success": True, "message": "Deployed", "copied": []}


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    svc = FakeNtService()
    deploy = FakeDeployService()
    logger = RecordingLogger()
    register_nt_routes(app, svc, deploy, logger)
    app.config["nt_service"] = svc
    app.config["deploy_service"] = deploy
    app.config["logger"] = logger
    return app


class TestNtRoutes:

    def test_install_netmq(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/install-netmq")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True

    def test_deploy_nt(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/deploy", json={"target_dir": "/some/custom"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert app.config["deploy_service"].last_target_dir == "/some/custom"

    def test_deploy_nt_exception_returns_500(self, app):
        app.config["deploy_service"].deploy_ninjatrader = MagicMock(side_effect=Exception("boom"))
        with app.test_client() as client:
            resp = client.post("/api/nt/deploy")
            assert resp.status_code == 500
            data = resp.get_json()
            assert "Internal server error" in data["message"]
            assert len(app.config["logger"].errors) == 1

    def test_open_nt(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/open", json={"username": "user1", "password": "pass1"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert "user1" in data["message"]
            assert app.config["nt_service"].last_username == "user1"
            assert app.config["nt_service"].last_password == "pass1"

    def test_open_nt_empty_credentials(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/open", json={})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert app.config["nt_service"].last_username == ""
            assert app.config["nt_service"].last_password == ""

    def test_open_nt_exception_returns_500(self, app):
        app.config["nt_service"].open_nt_and_login = MagicMock(side_effect=Exception("boom"))
        with app.test_client() as client:
            resp = client.post("/api/nt/open", json={"username": "u", "password": "p"})
            assert resp.status_code == 500
            data = resp.get_json()
            assert "Internal server error" in data["message"]
            assert len(app.config["logger"].errors) == 1
