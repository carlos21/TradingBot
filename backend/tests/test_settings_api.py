"""Tests for settings HTTP API endpoints."""
import pytest
from flask import Flask

from src.controllers.settings_controller import SettingsController
from src.infrastructure.database.database import setup_database
from src.infrastructure.repositories.accounts_repository import NtAccountRepository
from src.infrastructure.repositories.credentials_repository import CredentialRepository
from src.infrastructure.repositories.settings_repository import SettingsRepository
from src.routes.settings_routes import register_settings_routes
from src.services.settings_service import SettingsService
from tests.fakes import FakeLogger


@pytest.fixture
def app(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'api_test.db'}"
    setup_database(db_url=db_path)
    from src.infrastructure.database.database import get_db_session
    session = get_db_session().__enter__()

    settings_repo = SettingsRepository()
    accounts_repo = NtAccountRepository()
    creds_repo = CredentialRepository()
    svc = SettingsService(settings_repo, accounts_repo, creds_repo)
    ctrl = SettingsController(svc)

    flask_app = Flask(__name__)
    register_settings_routes(flask_app, ctrl, FakeLogger())

    yield flask_app
    session.close()


@pytest.fixture
def client(app):
    return app.test_client()


class TestSettingsApi:
    def test_get_settings_empty(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trading"]["pair"] == "MNQ"
        assert data["accounts"] == []

    def test_save_and_get_settings(self, client):
        payload = {
            "trading": {"pair": "ES", "instrument": "ES 06-26", "risk_per_trade": "200", "risk_pct_per_trade": "", "rr_ratio": "4.0"},
            "network": {"flask_port": "5002", "zmq_host": "0.0.0.0", "zmq_market_port": "5555", "zmq_command_port": "5556", "zmq_query_port": "5557", "zmq_heartbeat_port": "5558"},
            "accounts": [{"name": "A1", "risk_usd": 150.0, "risk_pct": None, "live_enabled": False}],
            "credentials": {"username": "ntuser", "password": "ntpass"},
        }
        resp = client.post("/api/settings", json=payload)
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trading"]["pair"] == "ES"
        assert data["accounts"][0]["name"] == "A1"
        assert data["accounts"][0]["live_enabled"] is False

    def test_accounts_crud(self, client):
        resp = client.post("/api/accounts", json={"name": "A1", "risk_usd": 100.0})
        assert resp.status_code == 200

        resp = client.get("/api/accounts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1

        resp = client.delete("/api/accounts/A1")
        assert resp.status_code == 204

        resp = client.get("/api/accounts")
        assert resp.get_json() == []
