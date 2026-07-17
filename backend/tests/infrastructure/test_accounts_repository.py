"""Tests for src/infrastructure/repositories/accounts_repository.py."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.config.models import AccountConfig
from src.infrastructure.database.database import NtAccount, setup_database
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.repositories.accounts_repository import NtAccountRepository


@pytest.fixture
def repo(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'accounts_test.db'}"
    setup_database(db_url=db_path)
    return NtAccountRepository()


class _FakeDB(DatabaseProtocol):
    def __init__(self, session, engine):
        self._session = session
        self._engine = engine

    def get_session(self):
        return self._session

    def get_engine(self):
        return self._engine

    def create_tables(self, Base):
        pass

    @property
    def dialect(self) -> str:
        return "sqlite"


class TestNtAccountRepositoryGet:

    def test_get_account(self, repo):
        repo.upsert("Main", risk_usd=100.0, risk_pct=1.0, rr_ratio=3.0, live_enabled=True)
        account = repo.get_account("Main")
        assert account == AccountConfig(
            name="Main", risk_usd=100.0, risk_pct=1.0, rr_ratio=3.0, live_enabled=True
        )

    def test_get_account_missing(self, repo):
        assert repo.get_account("Missing") is None

    def test_get_account_treats_none_live_enabled_as_enabled(self, repo):
        # Simulate a legacy row where live_enabled is NULL
        with repo._session() as session:
            session.add(NtAccount(name="Legacy", risk_usd=50.0, live_enabled=None))
            session.commit()

        account = repo.get_account("Legacy")
        assert account.live_enabled is True

    def test_get_account_zero_live_enabled_is_disabled(self, repo):
        with repo._session() as session:
            session.add(NtAccount(name="Disabled", risk_usd=50.0, live_enabled=0))
            session.commit()

        account = repo.get_account("Disabled")
        assert account.live_enabled is False


class TestNtAccountRepositoryList:

    def test_list_accounts_empty(self, repo):
        assert repo.list_accounts() == []

    def test_list_accounts(self, repo):
        repo.upsert("A", risk_usd=100.0)
        repo.upsert("B", risk_pct=2.0, live_enabled=False)
        accounts = repo.list_accounts()
        assert len(accounts) == 2
        assert all(isinstance(a, AccountConfig) for a in accounts)
        names = {a.name for a in accounts}
        assert names == {"A", "B"}


class TestNtAccountRepositoryUpsert:

    def test_upsert_inserts(self, repo):
        repo.upsert("Main", risk_usd=100.0, risk_pct=1.0, rr_ratio=5.0, live_enabled=True)
        account = repo.get_account("Main")
        assert account.risk_usd == 100.0
        assert account.rr_ratio == 5.0

    def test_upsert_updates(self, repo):
        repo.upsert("Main", risk_usd=100.0, rr_ratio=3.0)
        repo.upsert("Main", risk_usd=200.0, rr_ratio=4.0, live_enabled=False)
        account = repo.get_account("Main")
        assert account.risk_usd == 200.0
        assert account.rr_ratio == 4.0
        assert account.live_enabled is False

    def test_upsert_stores_instrument_symbols(self, repo):
        repo.upsert(
            "Main",
            risk_usd=100.0,
            rr_ratio=3.0,
            instrument_symbols=["MNQ", "ES"],
        )
        account = repo.get_account("Main")
        assert account.instrument_symbols == ["MNQ", "ES"]

    def test_get_account_normalizes_instrument_symbols(self, repo):
        repo.upsert(
            "Main",
            risk_usd=100.0,
            rr_ratio=3.0,
            instrument_symbols=[" mNq ", "es", ""],
        )
        account = repo.get_account("Main")
        assert account.instrument_symbols == ["MNQ", "ES"]

    def test_upsert_defaults_instrument_symbols_to_empty_list(self, repo):
        repo.upsert("Main", risk_usd=100.0, rr_ratio=3.0)
        account = repo.get_account("Main")
        assert account.instrument_symbols == []

    def test_upsert_persists_live_disabled(self, repo):
        repo.upsert("Main", live_enabled=False)
        with repo._session() as session:
            row = session.query(NtAccount).filter_by(name="Main").one()
            assert row.live_enabled == 0

    def test_upsert_rolls_back_on_commit_failure(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'rollback_test.db'}"
        setup_database(db_url=db_path)
        real_session = setup_database.__module__  # not used
        from src.infrastructure.database.database import db

        real_session = db.get_session()
        real_session.commit = MagicMock(side_effect=RuntimeError("boom"))
        real_session.rollback = MagicMock()
        fake_db = _FakeDB(real_session, db.get_engine())
        repo = NtAccountRepository(db=fake_db)

        with pytest.raises(RuntimeError, match="boom"):
            repo.upsert("Main", risk_usd=100.0)

        real_session.rollback.assert_called_once()


class TestNtAccountRepositoryDelete:

    def test_delete_existing(self, repo):
        repo.upsert("Main", risk_usd=100.0)
        repo.delete("Main")
        assert repo.get_account("Main") is None

    def test_delete_missing_is_noop(self, repo):
        # Should not raise
        repo.delete("Missing")

    def test_delete_rolls_back_on_commit_failure(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'rollback_delete.db'}"
        setup_database(db_url=db_path)
        repo_existing = NtAccountRepository()
        repo_existing.upsert("Main", risk_usd=100.0)

        from src.infrastructure.database.database import db

        real_session = db.get_session()
        real_session.commit = MagicMock(side_effect=RuntimeError("boom"))
        real_session.rollback = MagicMock()
        fake_db = _FakeDB(real_session, db.get_engine())
        repo = NtAccountRepository(db=fake_db)

        with pytest.raises(RuntimeError, match="boom"):
            repo.delete("Main")

        real_session.rollback.assert_called_once()


class TestNtAccountRepositoryClearAll:

    def test_clear_all(self, repo):
        repo.upsert("A", risk_usd=100.0)
        repo.upsert("B", risk_usd=200.0)
        repo.clear_all()
        assert repo.list_accounts() == []

    def test_clear_all_rolls_back_on_commit_failure(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'rollback_clear.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import db

        real_session = db.get_session()
        real_session.commit = MagicMock(side_effect=RuntimeError("boom"))
        real_session.rollback = MagicMock()
        fake_db = _FakeDB(real_session, db.get_engine())
        repo = NtAccountRepository(db=fake_db)

        with pytest.raises(RuntimeError, match="boom"):
            repo.clear_all()

        real_session.rollback.assert_called_once()
