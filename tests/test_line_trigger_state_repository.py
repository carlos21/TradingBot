"""Tests for src/repositories/line_trigger_state_repository.py."""

import pytest

from src.database.database import setup_database
from src.repositories.line_trigger_state_repository import (
    InMemoryLineTriggerStateRepository,
    SQLiteLineTriggerStateRepository,
)


@pytest.fixture
def sql_repo(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'trigger_state_test.db'}"
    setup_database(db_url=db_path)
    return SQLiteLineTriggerStateRepository()


@pytest.fixture
def mem_repo():
    return InMemoryLineTriggerStateRepository()


class TestSQLiteLineTriggerStateRepository:

    def test_save_and_load(self, sql_repo):
        sql_repo.save("L1", "MNQ", {"crosses": 2, "latched": True})
        state = sql_repo.load("L1")
        assert state == {"crosses": 2, "latched": True}

    def test_load_missing(self, sql_repo):
        assert sql_repo.load("NONEXISTENT") is None

    def test_update_existing(self, sql_repo):
        sql_repo.save("L1", "MNQ", {"crosses": 2})
        sql_repo.save("L1", "MNQ", {"crosses": 3, "triggered": True})
        state = sql_repo.load("L1")
        assert state == {"crosses": 3, "triggered": True}

    def test_load_all(self, sql_repo):
        sql_repo.save("L1", "MNQ", {"crosses": 2})
        sql_repo.save("L2", "MNQ", {"crosses": 1})
        sql_repo.save("L3", "ES", {"crosses": 3})
        states = sql_repo.load_all("MNQ")
        assert len(states) == 2
        assert "L1" in states
        assert "L2" in states
        assert "L3" not in states

    def test_delete(self, sql_repo):
        sql_repo.save("L1", "MNQ", {"crosses": 2})
        sql_repo.delete("L1")
        assert sql_repo.load("L1") is None

    def test_delete_missing_no_error(self, sql_repo):
        sql_repo.delete("NONEXISTENT")  # Should not raise


class TestInMemoryLineTriggerStateRepository:

    def test_save_and_load(self, mem_repo):
        mem_repo.save("L1", "MNQ", {"crosses": 2})
        state = mem_repo.load("L1")
        assert state == {"crosses": 2}

    def test_load_missing(self, mem_repo):
        assert mem_repo.load("NONEXISTENT") is None

    def test_update_existing(self, mem_repo):
        mem_repo.save("L1", "MNQ", {"crosses": 2})
        mem_repo.save("L1", "MNQ", {"crosses": 3})
        state = mem_repo.load("L1")
        assert state == {"crosses": 3}

    def test_load_all(self, mem_repo):
        mem_repo.save("L1", "MNQ", {"crosses": 2})
        mem_repo.save("L2", "MNQ", {"crosses": 1})
        mem_repo.save("L3", "ES", {"crosses": 3})
        states = mem_repo.load_all("MNQ")
        assert len(states) == 3  # In-memory returns all, not filtered by pair
        assert "L1" in states
        assert "L2" in states
        assert "L3" in states

    def test_delete(self, mem_repo):
        mem_repo.save("L1", "MNQ", {"crosses": 2})
        mem_repo.delete("L1")
        assert mem_repo.load("L1") is None

    def test_delete_missing_no_error(self, mem_repo):
        mem_repo.delete("NONEXISTENT")  # Should not raise
