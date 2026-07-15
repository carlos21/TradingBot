"""End-to-end tests for NinjaTrader / MetaTrader connection routes.

These tests register the real MT/NT route modules on a Flask app with fake
manager/deploy services and exercise the HTTP surface that the frontend uses
to launch or deploy each platform.
"""

import os

import pytest
from flask import Flask

from src.routes.mt_routes import register_mt_routes
from src.routes.nt_routes import register_nt_routes
from tests.fakes import FakeLogger


class FakeNtService:
    def open_nt_and_login(self, username, password):
        return {"success": True, "message": f"Launched for {username}"}


class FakeMtService:
    def __init__(self):
        self.launch_calls = []

    def launch_terminal(self, exe_path=None):
        self.launch_calls.append(exe_path)
        return {"success": True, "message": "MT launched"}


class FakeDeployService:
    def __init__(self):
        self.deploy_nt_calls = []
        self.deploy_mt_calls = []

    def deploy_ninjatrader(self, target_dir=None):
        self.deploy_nt_calls.append(target_dir)
        return {"success": True, "message": "NT deployed", "copied": []}

    def deploy_metatrader(self, target_dir=None, pair="NAS100", ports=None):
        self.deploy_mt_calls.append((target_dir, pair, ports))
        return {"success": True, "message": "MT deployed", "copied": []}


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    nt_service = FakeNtService()
    mt_service = FakeMtService()
    deploy = FakeDeployService()
    logger = FakeLogger()
    register_nt_routes(app, nt_service, deploy, logger)
    register_mt_routes(app, mt_service, deploy, logger)
    return app


class TestNinjaTraderConnectionE2E:
    def test_nt_deploy(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/deploy", json={"target_dir": "/tmp/nt"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True

    def test_nt_open(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/open", json={"username": "u", "password": "p"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert "u" in data["message"]

    def test_nt_install_netmq_legacy_endpoint(self, app):
        with app.test_client() as client:
            resp = client.post("/api/nt/install-netmq")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True


class TestMetaTraderConnectionE2E:
    def test_mt_launch_with_exe_path(self, app):
        with app.test_client() as client:
            resp = client.post("/api/mt/launch", json={"exe_path": "/tmp/mt/terminal64.exe"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True

    def test_mt_launch_auto_detect(self, app):
        with app.test_client() as client:
            resp = client.post("/api/mt/launch", json={})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True

    def test_mt_deploy(self, app):
        with app.test_client() as client:
            resp = client.post(
                "/api/mt/deploy",
                json={"target_dir": "/tmp/mt", "pair": "NAS100", "ports": {"cmd": "5555"}},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True


class TestPlatformDeployE2E:
    def test_both_platforms_deploy(self, app):
        with app.test_client() as client:
            nt_resp = client.post("/api/nt/deploy")
            mt_resp = client.post("/api/mt/deploy")
            assert nt_resp.status_code == 200
            assert mt_resp.status_code == 200
            assert nt_resp.get_json()["success"] is True
            assert mt_resp.get_json()["success"] is True
