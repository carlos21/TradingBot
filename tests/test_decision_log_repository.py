"""Tests for src/repositories/decision_log_repository.py."""

import pytest

from src.database.database import setup_database
from src.repositories.decision_log_repository import DecisionLogRepository


@pytest.fixture
def repo(tmp_path):
    """Create a DecisionLogRepository backed by a temporary SQLite DB."""
    db_path = f"sqlite:///{tmp_path / 'test_decisions.db'}"
    setup_database(db_url=db_path)
    return DecisionLogRepository()


class TestDecisionLogRepository:

    def test_add_and_get_recent(self, repo):
        repo.add_log(
            bar_time=1000.0,
            pair="MNQ",
            tf="5m",
            line_id="L1",
            event="LATCH",
            direction="long",
            reason="depth=6.0",
        )
        repo.add_log(
            bar_time=2000.0,
            pair="MNQ",
            tf="5m",
            line_id="L1",
            event="FILTER_BLOCK",
            filter_name="min_cross_depth",
            reason="depth=2.0 < 5.0",
        )

        recent = repo.get_recent(pair="MNQ", limit=10)
        assert len(recent) == 2
        # Most recent first (by id desc)
        assert recent[0]["event"] == "FILTER_BLOCK"
        assert recent[0]["filter_name"] == "min_cross_depth"
        assert recent[1]["event"] == "LATCH"
        assert recent[1]["direction"] == "long"

    def test_get_by_line_id(self, repo):
        repo.add_log(bar_time=1000.0, pair="MNQ", line_id="L1", event="LATCH")
        repo.add_log(bar_time=2000.0, pair="MNQ", line_id="L2", event="REMOVE")
        repo.add_log(bar_time=3000.0, pair="MNQ", line_id="L1", event="ENTRY")

        logs = repo.get_by_line_id("L1")
        assert len(logs) == 2
        assert logs[0]["event"] == "LATCH"
        assert logs[1]["event"] == "ENTRY"

    def test_filter_by_event(self, repo):
        repo.add_log(bar_time=1000.0, pair="MNQ", event="LATCH")
        repo.add_log(bar_time=2000.0, pair="MNQ", event="FILTER_BLOCK")
        repo.add_log(bar_time=3000.0, pair="MNQ", event="ENTRY")

        filtered = repo.get_recent(pair="MNQ", event="FILTER_BLOCK")
        assert len(filtered) == 1
        assert filtered[0]["event"] == "FILTER_BLOCK"

    def test_cleanup_old(self, repo):
        # Add a log with current time
        repo.add_log(bar_time=1000.0, pair="MNQ", event="LATCH")
        assert len(repo.get_recent(limit=100)) == 1

        # Cleanup with 0 days should delete everything older than now
        deleted = repo.cleanup_old(days=0)
        assert deleted == 1
        assert len(repo.get_recent(limit=100)) == 0

    def test_get_recent_default_limit(self, repo):
        for i in range(600):
            repo.add_log(bar_time=float(i), pair="MNQ", event="LATCH")
        recent = repo.get_recent()
        assert len(recent) == 500  # default limit
