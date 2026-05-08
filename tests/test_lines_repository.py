"""Tests for src/repositories/lines_repository.py."""

from datetime import datetime, timezone

import pytest

from src.database.database import setup_database
from src.dbexception import DBException, DBNotFoundException
from src.repositories.lines_repository import SQLLineRepository


@pytest.fixture
def repo(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'lines_test.db'}"
    setup_database(db_url=db_path)
    return SQLLineRepository()


class TestSQLLineRepository:

    def test_insert_line(self, repo):
        line = repo.insert_line(pair="MNQ", price=5000.0)
        assert line.pair == "MNQ"
        assert line.price == 5000.0
        assert line.line_id is not None
        assert line.creation_date is not None

    def test_insert_line_with_creation_date(self, repo):
        c_date = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        line = repo.insert_line(pair="MNQ", price=5000.0, creation_date=c_date)
        assert line.creation_date == c_date

    def test_get_line(self, repo):
        line = repo.insert_line(pair="MNQ", price=5000.0)
        fetched = repo.get_line(line.line_id)
        assert fetched is not None
        assert fetched.line_id == line.line_id
        assert fetched.price == 5000.0

    def test_get_line_missing(self, repo):
        assert repo.get_line("NONEXISTENT") is None

    def test_list_lines(self, repo):
        repo.insert_line(pair="MNQ", price=5000.0)
        repo.insert_line(pair="MNQ", price=5100.0)
        repo.insert_line(pair="ES", price=4000.0)
        lines = repo.list_lines("MNQ")
        assert len(lines) == 2
        prices = [l.price for l in lines]
        assert 5000.0 in prices
        assert 5100.0 in prices

    def test_list_lines_empty(self, repo):
        assert repo.list_lines("MNQ") == []

    def test_update_line(self, repo):
        line = repo.insert_line(pair="MNQ", price=5000.0)
        updated = repo.update_line(line.line_id, 5200.0)
        assert updated.price == 5200.0
        # Verify persistence
        fetched = repo.get_line(line.line_id)
        assert fetched.price == 5200.0

    def test_update_line_missing(self, repo):
        with pytest.raises(DBNotFoundException):
            repo.update_line("NONEXISTENT", 5200.0)

    def test_delete_line(self, repo):
        line = repo.insert_line(pair="MNQ", price=5000.0)
        repo.delete_line(line.line_id)
        assert repo.get_line(line.line_id) is None

    def test_ensure_utc_aware(self, repo):
        # The _ensure_utc_aware method is internal, but we test it via behavior
        line = repo.insert_line(pair="MNQ", price=5000.0)
        # creation_date should be timezone-aware
        assert line.creation_date.tzinfo is not None
