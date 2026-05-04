"""Tests for SettingsService."""
import pytest

from src.database.database import setup_database
from src.repositories.accounts_repository import NtAccountRepository
from src.repositories.credentials_repository import CredentialRepository
from src.repositories.settings_repository import SettingsRepository
from src.services.settings_service import SettingsService


@pytest.fixture
def db_session(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'svc_test.db'}"
    setup_database(db_url=db_path)
    from src.database.database import get_db_session
    session = get_db_session().__enter__()
    yield session
    session.close()


@pytest.fixture
def service(db_session):
    settings_repo = SettingsRepository()
    accounts_repo = NtAccountRepository()
    creds_repo = CredentialRepository()
    return SettingsService(settings_repo, accounts_repo, creds_repo, secret_key="test-secret-key-1234")


class TestSettingsService:
    def test_save_and_get_full_settings(self, service):
        payload = {
            "trading": {"pair": "ES", "instrument": "ES 06-26", "risk_per_trade": "200", "risk_pct_per_trade": "", "rr_ratio": "4.0"},
            "network": {"flask_port": "5002", "zmq_host": "0.0.0.0", "zmq_market_port": "5555", "zmq_command_port": "5556", "zmq_query_port": "5557", "zmq_heartbeat_port": "5558"},
            "accounts": [{"name": "A1", "risk_usd": 150.0, "risk_pct": None}],
            "credentials": {"username": "ntuser", "password": "ntpass"},
        }
        service.save_full_settings(payload)
        data = service.get_full_settings()
        assert data["trading"]["pair"] == "ES"
        assert data["accounts"][0]["name"] == "A1"
        assert data["credentials"]["username"] == "ntuser"
        assert data["credentials"]["password"] == "ntpass"

    def test_credentials_encrypted(self, service):
        service.save_full_settings({
            "trading": {}, "network": {}, "accounts": [],
            "credentials": {"username": "u", "password": "p"},
        })
        raw = service._creds.get_credential("ninjatrader")
        assert raw[0] == "u"
        # encrypted value should not equal plaintext
        assert raw[1] != "p"
        # decrypted value should equal plaintext
        assert service._decrypt(raw[1]) == "p"

    def test_to_app_config_overrides(self, service):
        service.save_full_settings({
            "trading": {"pair": "MNQ"},
            "network": {"zmq_host": "127.0.0.1"},
            "accounts": [{"name": "A1", "risk_usd": 100.0}],
            "credentials": {},
        })
        overrides = service.to_app_config_overrides()
        assert overrides["pair"] == "MNQ"
        assert len(overrides["nt_accounts"]) == 1
