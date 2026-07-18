"""Tests for settings HTTP API endpoints."""
import pytest
from flask import Flask
from sqlalchemy import create_engine, inspect, text

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


class _ExplodingSettingsService:
    def save_full_settings(self, payload):
        raise RuntimeError("service failure")


class TestSettingsApi:
    def test_get_settings_empty(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trading"]["pair"] == "MNQ"
        assert data["trading"]["instruments"][0]["symbol"] == "MNQ"
        assert data["accounts"] == []

    def test_save_and_get_settings(self, client):
        payload = {
            "trading": {"pair": "ES", "instrument": "MNQ 12-26", "risk_per_trade": "200", "risk_pct_per_trade": "", "rr_ratio": "4.0"},
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
        # Symbols are hardcoded in the catalog; only the full name is editable.
        assert data["trading"]["pair"] == "MNQ"
        assert data["trading"]["instruments"][0]["symbol"] == "MNQ"
        assert data["trading"]["instruments"][0]["full_name"] == "MNQ 12-26"
        assert data["accounts"][0]["name"] == "A1"
        assert data["accounts"][0]["live_enabled"] is False

    def test_save_and_get_instruments(self, client):
        payload = {
            "trading": {
                "instruments": [
                    {"symbol": "MNQ", "full_name": "MNQ 12-26"},
                    {"symbol": "MES", "full_name": "MES 12-26"},
                ],
                "session_end": "17:00",
            },
            "network": {},
            "accounts": [],
            "credentials": {},
        }
        resp = client.post("/api/settings", json=payload)
        assert resp.status_code == 200

        resp = client.get("/api/settings")
        data = resp.get_json()
        assert data["trading"]["instruments"] == [
            {"symbol": "MNQ", "full_name": "MNQ 12-26", "point_value": 2.0},
            {"symbol": "MES", "full_name": "MES 12-26", "point_value": 5.0},
        ]
        assert data["trading"]["pair"] == "MNQ"
        assert data["trading"]["instrument"] == "MNQ 12-26"
        assert data["trading"]["session_end"] == "17:00"

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

    def test_migration_adds_live_enabled_column(self, tmp_path):
        """Regression: older nt_accounts tables without live_enabled must migrate."""
        db_path = f"sqlite:///{tmp_path / 'legacy.db'}"
        engine = create_engine(db_path)
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE nt_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL,
                    risk_usd FLOAT,
                    risk_pct FLOAT,
                    rr_ratio FLOAT,
                    updated_at DATETIME
                )
            """))
            conn.commit()

        assert "live_enabled" not in {c["name"] for c in inspect(engine).get_columns("nt_accounts")}

        setup_database(db_url=db_path)

        assert "live_enabled" in {c["name"] for c in inspect(engine).get_columns("nt_accounts")}

        from src.infrastructure.database.database import get_db_session
        session = get_db_session().__enter__()
        settings_repo = SettingsRepository()
        accounts_repo = NtAccountRepository()
        creds_repo = CredentialRepository()
        svc = SettingsService(settings_repo, accounts_repo, creds_repo)
        ctrl = SettingsController(svc)

        flask_app = Flask(__name__)
        register_settings_routes(flask_app, ctrl, FakeLogger())

        with flask_app.test_client() as client:
            payload = {
                "trading": {},
                "network": {},
                "accounts": [{"name": "LegacyAcct", "risk_usd": 100.0, "live_enabled": True}],
                "credentials": {},
            }
            resp = client.post("/api/settings", json=payload)
            assert resp.status_code == 200, resp.get_json()

            resp = client.get("/api/settings")
            data = resp.get_json()
            assert len(data["accounts"]) == 1
            assert data["accounts"][0]["name"] == "LegacyAcct"
            assert data["accounts"][0]["live_enabled"] is True

        session.close()

    def test_save_settings_error_includes_message(self):
        """The controller should surface a useful error when the service fails."""
        from flask import jsonify

        ctrl = SettingsController(_ExplodingSettingsService())

        flask_app = Flask(__name__)
        register_settings_routes(flask_app, ctrl, FakeLogger())

        @flask_app.errorhandler(500)
        def _handle_500(error):
            return jsonify({"error": str(error.description)}), 500

        with flask_app.test_client() as client:
            resp = client.post("/api/settings", json={"accounts": []})
            assert resp.status_code == 500
            data = resp.get_json()
            assert "service failure" in data["error"]
