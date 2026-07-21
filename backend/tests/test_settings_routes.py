"""Tests for src/routes/settings_routes.py."""

import pytest
from flask import Flask

from src.routes.settings_routes import register_settings_routes
from tests.fakes import FakeLogger


class FakeSettingsController:
    def get_settings(self):
        return {"pair": "MNQ"}

    def save_settings(self):
        return {"success": True}

    def save_credentials(self):
        return {"success": True}

    def get_accounts(self):
        return []

    def save_account(self):
        return {"success": True}

    def delete_account(self, name):
        return {"success": True}


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    ctrl = FakeSettingsController()
    logger = FakeLogger()
    register_settings_routes(app, ctrl, logger)
    return app


class TestSettingsRoutes:

    def test_get_settings(self, app):
        with app.test_client() as client:
            resp = client.get("/api/settings")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["pair"] == "MNQ"

    def test_save_settings(self, app):
        with app.test_client() as client:
            resp = client.post("/api/settings")
            assert resp.status_code == 200

    def test_save_credentials(self, app):
        with app.test_client() as client:
            resp = client.post("/api/settings/credentials")
            assert resp.status_code == 200

    def test_get_accounts(self, app):
        with app.test_client() as client:
            resp = client.get("/api/accounts")
            assert resp.status_code == 200

    def test_delete_account(self, app):
        with app.test_client() as client:
            resp = client.delete("/api/accounts/test")
            assert resp.status_code == 200
