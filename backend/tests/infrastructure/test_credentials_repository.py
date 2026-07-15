"""Tests for src/infrastructure/repositories/credentials_repository.py."""

from unittest.mock import MagicMock

import pytest

from src.infrastructure.database.database import AppCredential, setup_database
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.repositories.credentials_repository import CredentialRepository


@pytest.fixture
def repo(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'credentials_test.db'}"
    setup_database(db_url=db_path)
    return CredentialRepository()


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


class TestCredentialRepositoryGet:

    def test_get_credential(self, repo):
        repo.save_credential("ninjatrader", "user1", "enc_pass_1")
        username, password = repo.get_credential("ninjatrader")
        assert username == "user1"
        assert password == "enc_pass_1"

    def test_get_credential_missing(self, repo):
        assert repo.get_credential("missing") is None

    def test_get_credential_returns_most_recently_updated(self, repo):
        from datetime import datetime, timezone

        repo.save_credential("ninjatrader", "old_user", "old_pass")
        # Force an older updated_at so the test is deterministic regardless
        # of SQLite default precision.
        with repo._session() as session:
            old = session.query(AppCredential).filter_by(username="old_user").one()
            old.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
            session.commit()

        repo.save_credential("ninjatrader", "new_user", "new_pass")
        username, password = repo.get_credential("ninjatrader")
        assert username == "new_user"
        assert password == "new_pass"


class TestCredentialRepositoryList:

    def test_list_all_empty(self, repo):
        assert repo.list_all() == []

    def test_list_all(self, repo):
        repo.save_credential("ninjatrader", "user1", "enc1")
        repo.save_credential("metatrader", "user2", "enc2")
        result = repo.list_all()
        assert len(result) == 2
        assert {"service": "ninjatrader", "username": "user1"} in result
        assert {"service": "metatrader", "username": "user2"} in result


class TestCredentialRepositorySave:

    def test_save_credential_inserts(self, repo):
        repo.save_credential("ninjatrader", "user1", "enc_pass")
        assert repo.get_credential("ninjatrader") == ("user1", "enc_pass")

    def test_save_credential_updates_existing(self, repo):
        repo.save_credential("ninjatrader", "user1", "old_pass")
        repo.save_credential("ninjatrader", "user1", "new_pass")
        assert repo.get_credential("ninjatrader") == ("user1", "new_pass")
        assert repo.list_all() == [{"service": "ninjatrader", "username": "user1"}]

    def test_save_credential_allows_multiple_usernames_per_service(self, repo):
        repo.save_credential("ninjatrader", "user1", "pass1")
        repo.save_credential("ninjatrader", "user2", "pass2")
        usernames = {r.username for r in repo._session().__enter__().query(AppCredential).all()}
        assert usernames == {"user1", "user2"}

    def test_save_credential_rolls_back_on_commit_failure(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'cred_rollback.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import db

        real_session = db.get_session()
        real_session.commit = MagicMock(side_effect=RuntimeError("boom"))
        real_session.rollback = MagicMock()
        fake_db = _FakeDB(real_session, db.get_engine())
        repo = CredentialRepository(db=fake_db)

        with pytest.raises(RuntimeError, match="boom"):
            repo.save_credential("ninjatrader", "user1", "enc_pass")

        real_session.rollback.assert_called_once()


class TestCredentialRepositoryDelete:

    def test_delete_credential(self, repo):
        repo.save_credential("ninjatrader", "user1", "enc_pass")
        repo.delete_credential("ninjatrader")
        assert repo.get_credential("ninjatrader") is None
        assert repo.list_all() == []

    def test_delete_credential_missing_is_noop(self, repo):
        # Should not raise
        repo.delete_credential("missing")

    def test_delete_credential_rolls_back_on_commit_failure(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'cred_delete_rollback.db'}"
        setup_database(db_url=db_path)
        existing = CredentialRepository()
        existing.save_credential("ninjatrader", "user1", "enc_pass")

        from src.infrastructure.database.database import db

        real_session = db.get_session()
        real_session.commit = MagicMock(side_effect=RuntimeError("boom"))
        real_session.rollback = MagicMock()
        fake_db = _FakeDB(real_session, db.get_engine())
        repo = CredentialRepository(db=fake_db)

        with pytest.raises(RuntimeError, match="boom"):
            repo.delete_credential("ninjatrader")

        real_session.rollback.assert_called_once()
