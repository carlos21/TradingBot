"""Tests for settings repositories."""
import pytest

from src.database.database import setup_database
from src.repositories.accounts_repository import NtAccountRepository
from src.repositories.credentials_repository import CredentialRepository
from src.repositories.settings_repository import SettingsRepository


@pytest.fixture
def db_session(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'settings_test.db'}"
    setup_database(db_url=db_path)
    from src.database.database import get_db_session
    session = get_db_session().__enter__()
    yield session
    session.close()


class TestSettingsRepository:
    def test_set_and_get(self, db_session):
        repo = SettingsRepository()
        repo.set("pair", "MNQ")
        assert repo.get("pair") == "MNQ"

    def test_update_existing(self, db_session):
        repo = SettingsRepository()
        repo.set("pair", "MNQ")
        repo.set("pair", "ES")
        assert repo.get("pair") == "ES"

    def test_get_all(self, db_session):
        repo = SettingsRepository()
        repo.set("pair", "MNQ")
        repo.set("zmq_host", "127.0.0.1")
        all_settings = repo.get_all()
        assert all_settings == {"pair": "MNQ", "zmq_host": "127.0.0.1"}

    def test_delete(self, db_session):
        repo = SettingsRepository()
        repo.set("pair", "MNQ")
        repo.delete("pair")
        assert repo.get("pair") is None


class TestNtAccountRepository:
    def test_upsert_and_list(self, db_session):
        repo = NtAccountRepository()
        repo.upsert("Acct1", risk_usd=100.0)
        accounts = repo.list_accounts()
        assert len(accounts) == 1
        assert accounts[0].name == "Acct1"
        assert accounts[0].risk_usd == 100.0

    def test_delete(self, db_session):
        repo = NtAccountRepository()
        repo.upsert("Acct1", risk_usd=100.0)
        repo.delete("Acct1")
        assert repo.list_accounts() == []

    def test_clear_all(self, db_session):
        repo = NtAccountRepository()
        repo.upsert("Acct1")
        repo.upsert("Acct2")
        repo.clear_all()
        assert repo.list_accounts() == []


class TestCredentialRepository:
    def test_save_and_get(self, db_session):
        repo = CredentialRepository()
        repo.save_credential("ninjatrader", "user1", "enc_pass")
        cred = repo.get_credential("ninjatrader")
        assert cred == ("user1", "enc_pass")

    def test_get_missing(self, db_session):
        repo = CredentialRepository()
        assert repo.get_credential("ninjatrader") is None
