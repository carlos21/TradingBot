"""Tests for DbConfigLoader."""
import pytest

from src.config.loaders import DbConfigLoader
from src.infrastructure.database.database import setup_database
from src.infrastructure.repositories.accounts_repository import NtAccountRepository
from src.infrastructure.repositories.settings_repository import SettingsRepository


@pytest.fixture
def db_loader(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'loader_test.db'}"
    setup_database(db_url=db_path)
    from src.infrastructure.database.database import get_db_session
    get_db_session().__enter__()

    settings_repo = SettingsRepository()
    accounts_repo = NtAccountRepository()
    settings_repo.set("pair", "ES")
    settings_repo.set("zmq_host", "0.0.0.0")
    settings_repo.set("flask_port", "5002")
    accounts_repo.upsert("A1", risk_usd=100.0)
    return DbConfigLoader(db_path=db_path)


class TestDbConfigLoader:
    def test_loads_settings(self, db_loader):
        cfg = db_loader.load()
        assert cfg.pair == "ES"
        assert cfg.zmq_host == "0.0.0.0"
        assert cfg.flask_port == 5002

    def test_instruments_setting_does_not_override_config(self, tmp_path):
        """The ``instruments`` setting belongs to the instrument registry;
        DbConfigLoader must not apply it to AppConfig (no default instrument)."""
        db_path = f"sqlite:///{tmp_path / 'instruments_loader.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        session = get_db_session().__enter__()

        settings_repo = SettingsRepository()
        settings_repo.set(
            "instruments",
            '[{"symbol": "NQ", "full_name": "NQ 09-26", "point_value": 5.0}]',
        )
        session.close()

        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.pair == "MNQ"       # AppConfig default, untouched
        assert cfg.point_value == 2.0  # AppConfig default, untouched

    def test_loads_accounts(self, db_loader):
        cfg = db_loader.load()
        assert len(cfg.nt_accounts) == 1
        assert cfg.nt_accounts[0].name == "A1"
        assert cfg.nt_accounts[0].risk_usd == 100.0

    def test_fallback_on_missing_db(self):
        loader = DbConfigLoader(db_path="sqlite:///./nonexistent.db")
        cfg = loader.load()
        # Should return defaults without crashing
        assert cfg.pair == "MNQ"  # AppConfig default
