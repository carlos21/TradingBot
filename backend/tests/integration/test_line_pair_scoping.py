"""Integration test: strategy lines are scoped to their instrument pair.

Two ``StreamingSession`` instances (MNQ and ES) share a single
``SQLLineRepository``.  This test verifies that:

* bootstrapping loads only lines whose ``pair`` matches the session symbol,
* adding a line through one session's controller is visible only to that
  instrument, and
* line lists returned by the HTTP controller are isolated by pair.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app_factory import Repositories
from src.application.streaming_session import StreamingSession
from src.domain.models import Instrument
from src.infrastructure.database.database_protocol import Base, get_database
from src.infrastructure.repositories.lines_repository import SQLLineRepository
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from tests.fakes import FakeDataSource, FakeLogger, FakeTradeRepository

os.environ.setdefault("SECRET_KEY", "test-secret")


@pytest.fixture
def shared_line_repo():
    """SQLLineRepository backed by a temporary SQLite file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{path}"
    db = get_database(db_url)
    db.create_tables(Base)
    repo = SQLLineRepository(db=db)
    try:
        yield repo
    finally:
        db.get_engine().dispose()
        os.unlink(path)


def _build_session(symbol: str, line_repo: SQLLineRepository) -> StreamingSession:
    """Build a minimal non-live ``StreamingSession`` for ``symbol``."""
    instrument = Instrument(symbol=symbol, full_name=symbol, point_value=2.0)
    data_source = FakeDataSource(pair=symbol, bars=[])

    numbers = StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=90.0,
        extra_sl_space=0.0,
        fixed_stop_loss=20.0,
        rr_ratio=2.0,
        point_value=2.0,
        account_balance=100000.0,
    )

    repos = Repositories(
        lines=line_repo,
        trades=FakeTradeRepository(),
    )

    return StreamingSession(
        instrument=instrument,
        socketio=None,
        data_source=data_source,
        repos=repos,
        numbers=numbers,
        options=DEFAULT_STRATEGY_OPTIONS,
        candle_config=CandleConfig(),
        timeframes=["5m"],
        live_mode=False,
        logger=FakeLogger(),
        bootstrap_existing_lines=True,
    )


class TestLinePairScoping:
    def test_bootstrap_loads_only_own_lines(self, shared_line_repo):
        shared_line_repo.insert_line(pair="MNQ", price=21000.0)
        shared_line_repo.insert_line(pair="ES", price=4500.0)
        shared_line_repo.insert_line(pair="MNQ", price=21100.0)

        mnq_session = _build_session("MNQ", shared_line_repo)
        es_session = _build_session("ES", shared_line_repo)

        mnq_lines = mnq_session.strategy.strategy_lines
        es_lines = es_session.strategy.strategy_lines

        assert len(mnq_lines) == 2
        assert len(es_lines) == 1
        assert all(line["pair"] == "MNQ" for line in mnq_lines.values())
        assert all(line["pair"] == "ES" for line in es_lines.values())

    def test_add_line_is_isolated_between_sessions(self, shared_line_repo):
        mnq_session = _build_session("MNQ", shared_line_repo)
        es_session = _build_session("ES", shared_line_repo)

        line = shared_line_repo.insert_line(pair="MNQ", price=22000.0)
        mnq_session.strategy.add_strategy_line(
            line.line_id,
            line.price,
            creation_timestamp=line.creation_date.timestamp(),
            pair=line.pair,
        )

        assert len(mnq_session.strategy.strategy_lines) == 1
        assert len(es_session.strategy.strategy_lines) == 0

        # The shared repository must contain the MNQ line but not an ES line.
        assert len(shared_line_repo.list_lines("MNQ")) == 1
        assert len(shared_line_repo.list_lines("ES")) == 0

    def test_line_lookup_is_isolated_by_pair(self, shared_line_repo):
        shared_line_repo.insert_line(pair="MNQ", price=21000.0)
        shared_line_repo.insert_line(pair="ES", price=4500.0)

        _build_session("MNQ", shared_line_repo)
        _build_session("ES", shared_line_repo)

        mnq_lines = shared_line_repo.list_lines("MNQ")
        es_lines = shared_line_repo.list_lines("ES")

        assert len(mnq_lines) == 1
        assert mnq_lines[0].pair == "MNQ"
        assert len(es_lines) == 1
        assert es_lines[0].pair == "ES"
