"""Tests for src/routes/nt_routes.py."""

import pytest
from flask import Flask

from src.routes.nt_routes import register_nt_routes
from tests.fakes import FakeLogger


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


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    svc = FakeNtService()
    logger = FakeLogger()
    register_nt_routes(app, svc, logger)
    return app


class TestNtRoutes:

    def test_install_netmq(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/install-netmq")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True

    def test_open_nt(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/open", json={"username": "user1", "password": "pass1"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert "user1" in data["message"]

    def test_open_nt_empty_credentials(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/open", json={})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
