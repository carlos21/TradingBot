"""Tests for src/repositories/trades_repository.py."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.dbexception import DBNotFoundException
from src.infrastructure.database.database import setup_database
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.repositories.trades_repository import SQLTradeRepository


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


@pytest.fixture
def repo(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'trades_test.db'}"
    setup_database(db_url=db_path)
    return SQLTradeRepository()


def _make_trade(repo, **kwargs):
    defaults = {
        "pair": "MNQ",
        "trade_type": "long",
        "entry_price": 5000.0,
        "stop_loss": 4900.0,
        "take_profit": 5200.0,
        "risk": 100.0,
        "entry_time": datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return repo.insert_trade(**defaults)


class TestSQLTradeRepositoryInsert:

    def test_insert_trade(self, repo):
        trade = _make_trade(repo)
        assert trade.trade_id is not None
        assert trade.pair == "MNQ"
        assert trade.entry_price == 5000.0

    def test_insert_trade_with_custom_id(self, repo):
        trade = _make_trade(repo, trade_id="CUSTOM_T1")
        assert trade.trade_id == "CUSTOM_T1"

    def test_insert_trade_with_optional_fields(self, repo):
        trade = _make_trade(repo, risk_dollars=200.0, risk_pct=0.2, contracts=2.0, params={"rr": "1:3"})
        assert trade.risk_dollars == 200.0
        assert trade.risk_pct == 0.2
        assert trade.contracts == 2.0


class TestSQLTradeRepositoryGet:

    def test_get_trade(self, repo):
        trade = _make_trade(repo)
        fetched = repo.get_trade(trade.trade_id)
        assert fetched is not None
        assert fetched.trade_id == trade.trade_id
        assert fetched.entry_price == 5000.0

    def test_get_trade_missing(self, repo):
        assert repo.get_trade("NONEXISTENT") is None

    def test_list_trades(self, repo):
        _make_trade(repo, pair="MNQ")
        _make_trade(repo, pair="MNQ", entry_price=5100.0)
        _make_trade(repo, pair="ES", entry_price=4000.0)
        trades = repo.list_trades("MNQ")
        assert len(trades) == 2

    def test_list_trades_empty(self, repo):
        assert repo.list_trades("MNQ") == []

    def test_get_all_trades(self, repo):
        _make_trade(repo, pair="MNQ")
        trades = repo.get_all_trades("MNQ")
        assert len(trades) == 1


class TestSQLTradeRepositoryClose:

    def test_close_trade(self, repo):
        trade = _make_trade(repo)
        repo.close_trade(
            trade_id=trade.trade_id,
            exit_price=5200.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=2.0,
            result_type="TP",
            fees=4.0,
            pnl_usd=396.0,
        )
        fetched = repo.get_trade(trade.trade_id)
        assert fetched.exit_price == 5200.0
        assert fetched.result == 2.0
        assert fetched.result_type == "TP"
        assert fetched.fees == 4.0
        assert fetched.pnl_usd == 396.0

    def test_close_trade_with_naive_datetime(self, repo):
        trade = _make_trade(repo)
        repo.close_trade(
            trade_id=trade.trade_id,
            exit_price=5200.0,
            exit_time=datetime(2024, 1, 1, 11, 0),  # naive
            result=2.0,
            result_type="TP",
        )
        fetched = repo.get_trade(trade.trade_id)
        assert fetched.exit_time is not None
        assert fetched.exit_time.tzinfo is not None


class TestSQLTradeRepositoryUpdates:

    def test_update_stop_loss(self, repo):
        trade = _make_trade(repo)
        repo.update_stop_loss(trade.trade_id, 4950.0)
        fetched = repo.get_trade(trade.trade_id)
        assert fetched.stop_loss == 4950.0

    def test_update_stop_loss_missing_raises(self, repo):
        with pytest.raises(DBNotFoundException):
            repo.update_stop_loss("NONEXISTENT", 4950.0)

    def test_update_take_profit(self, repo):
        trade = _make_trade(repo)
        repo.update_take_profit(trade.trade_id, 5300.0)
        fetched = repo.get_trade(trade.trade_id)
        assert fetched.take_profit == 5300.0

    def test_update_take_profit_missing_raises(self, repo):
        with pytest.raises(DBNotFoundException):
            repo.update_take_profit("NONEXISTENT", 5300.0)

    def test_update_entry_price(self, repo):
        trade = _make_trade(repo)
        repo.update_entry_price(trade.trade_id, 5010.0)
        fetched = repo.get_trade(trade.trade_id)
        assert fetched.entry_price == 5010.0

    def test_update_entry_price_missing_raises(self, repo):
        with pytest.raises(DBNotFoundException):
            repo.update_entry_price("NONEXISTENT", 5010.0)

    def test_update_risk_fields(self, repo):
        trade = _make_trade(repo)
        repo.update_risk_fields(trade.trade_id, 150.0, 300.0, 0.3)
        fetched = repo.get_trade(trade.trade_id)
        assert fetched.risk == 150.0
        assert fetched.risk_dollars == 300.0
        assert fetched.risk_pct == 0.3

    def test_update_risk_fields_missing_raises(self, repo):
        with pytest.raises(DBNotFoundException):
            repo.update_risk_fields("NONEXISTENT", 150.0, 300.0, 0.3)

    def test_update_contracts(self, repo):
        trade = _make_trade(repo)
        repo.update_contracts(trade.trade_id, 3.0)
        fetched = repo.get_trade(trade.trade_id)
        assert fetched.contracts == 3.0

    def test_update_contracts_missing_raises(self, repo):
        with pytest.raises(DBNotFoundException):
            repo.update_contracts("NONEXISTENT", 3.0)

    def test_update_account_balance(self, repo):
        trade = _make_trade(repo)
        repo.update_account_balance(trade.trade_id, 150000.0)
        fetched = repo.get_trade(trade.trade_id)
        assert fetched.account_balance == 150000.0

    def test_update_account_balance_missing_raises(self, repo):
        with pytest.raises(DBNotFoundException):
            repo.update_account_balance("NONEXISTENT", 150000.0)


class TestSQLTradeRepositoryLogs:

    def test_append_and_get_trade_logs(self, repo):
        trade = _make_trade(repo)
        repo.append_trade_log(trade.trade_id, "OPEN", "Trade opened")
        repo.append_trade_log(trade.trade_id, "UPDATE_SL", "SL updated to 4950")
        logs = repo.get_trade_logs(trade.trade_id)
        assert len(logs) == 2
        assert logs[0]["event"] == "OPEN"
        assert logs[1]["event"] == "UPDATE_SL"

    def test_append_trade_log_fallback_path(self, repo):
        trade = _make_trade(repo)
        original_engine = repo._engine

        def failing_engine():
            raise RuntimeError("JSON fast path unavailable")

        repo._engine = failing_engine
        try:
            repo.append_trade_log(trade.trade_id, "OPEN", "Fallback log")
        finally:
            repo._engine = original_engine

        logs = repo.get_trade_logs(trade.trade_id)
        assert len(logs) == 1
        assert logs[0]["event"] == "OPEN"
        assert logs[0]["msg"] == "Fallback log"

    def test_get_trade_logs_missing(self, repo):
        assert repo.get_trade_logs("NONEXISTENT") == []


class TestSQLTradeRepositoryClosedTrades:

    def test_list_trades_includes_closed(self, repo):
        trade = _make_trade(repo)
        repo.close_trade(
            trade_id=trade.trade_id,
            exit_price=5200.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=2.0,
            result_type="TP",
        )
        trades = repo.list_trades("MNQ")
        assert len(trades) == 1
        assert trades[0].exit_price == 5200.0
        assert trades[0].result == 2.0
        assert trades[0].result_type == "TP"


class TestSQLTradeRepositoryDelete:

    def test_delete_trade(self, repo):
        trade = _make_trade(repo)
        repo.delete_trade(trade.trade_id)
        assert repo.get_trade(trade.trade_id) is None

    def test_delete_trade_cascades_to_child_trades(self, repo):
        parent = _make_trade(repo, trade_id="SIGNAL_1")
        child = _make_trade(repo, trade_id="ACCT_1", signal_id=parent.trade_id)
        unrelated = _make_trade(repo, trade_id="OTHER_1")

        repo.delete_trade(parent.trade_id)

        assert repo.get_trade(parent.trade_id) is None
        assert repo.get_trade(child.trade_id) is None
        assert repo.get_trade(unrelated.trade_id) is not None

    def test_delete_trade_missing_raises(self, repo):
        with pytest.raises(DBNotFoundException):
            repo.delete_trade("NONEXISTENT")


class TestSQLTradeRepositoryDeleteBatch:

    def test_delete_trades_removes_multiple_trades(self, repo):
        t1 = _make_trade(repo, trade_id="T1")
        t2 = _make_trade(repo, trade_id="T2")
        t3 = _make_trade(repo, trade_id="T3")

        repo.delete_trades([t1.trade_id, t2.trade_id])

        assert repo.get_trade(t1.trade_id) is None
        assert repo.get_trade(t2.trade_id) is None
        assert repo.get_trade(t3.trade_id) is not None

    def test_delete_trades_cascades_to_child_trades(self, repo):
        parent = _make_trade(repo, trade_id="SIGNAL_1")
        child = _make_trade(repo, trade_id="ACCT_1", signal_id=parent.trade_id)
        unrelated = _make_trade(repo, trade_id="OTHER_1")

        repo.delete_trades([parent.trade_id])

        assert repo.get_trade(parent.trade_id) is None
        assert repo.get_trade(child.trade_id) is None
        assert repo.get_trade(unrelated.trade_id) is not None

    def test_delete_trades_empty_list_is_noop(self, repo):
        trade = _make_trade(repo, trade_id="T1")
        repo.delete_trades([])
        assert repo.get_trade(trade.trade_id) is not None

    def test_delete_trades_missing_raises(self, repo):
        _make_trade(repo, trade_id="T1")
        with pytest.raises(DBNotFoundException):
            repo.delete_trades(["T1", "MISSING"])

        # T1 should remain because the batch is atomic
        assert repo.get_trade("T1") is not None


class TestSQLTradeRepositoryClear:

    def test_clear(self, repo):
        _make_trade(repo, trade_id="T1")
        _make_trade(repo, trade_id="T2")
        repo.clear()
        assert repo.list_trades("MNQ") == []


class TestSQLTradeRepositoryErrorPaths:

    def _failing_repo(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'failing.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import db

        real_session = db.get_session()
        real_session.commit = MagicMock(side_effect=RuntimeError("boom"))
        real_session.rollback = MagicMock()
        fake_db = _FakeDB(real_session, db.get_engine())
        return SQLTradeRepository(db=fake_db), real_session

    def test_insert_trade_raises_db_exception_on_commit_failure(self, tmp_path):
        repo, session = self._failing_repo(tmp_path)
        with pytest.raises(Exception) as exc_info:
            _make_trade(repo)
        assert "boom" in str(exc_info.value)
        session.rollback.assert_called()

    def test_close_trade_raises_db_exception_on_commit_failure(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'close_fail.db'}"
        setup_database(db_url=db_path)
        trade = SQLTradeRepository().insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=5000.0,
            stop_loss=4900.0,
            take_profit=5200.0,
            risk=100.0,
            entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
        )

        from src.infrastructure.database.database import db

        real_session = db.get_session()
        real_session.commit = MagicMock(side_effect=RuntimeError("boom"))
        real_session.rollback = MagicMock()
        fake_db = _FakeDB(real_session, db.get_engine())
        repo = SQLTradeRepository(db=fake_db)

        with pytest.raises(Exception) as exc_info:
            repo.close_trade(
                trade_id=trade.trade_id,
                exit_price=5200.0,
                exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
                result=2.0,
            )
        assert "boom" in str(exc_info.value)
        real_session.rollback.assert_called()

    def test_clear_raises_db_exception_on_commit_failure(self, tmp_path):
        repo, session = self._failing_repo(tmp_path)
        with pytest.raises(Exception) as exc_info:
            repo.clear()
        assert "boom" in str(exc_info.value)
        session.rollback.assert_called()

    def test_delete_trades_raises_db_exception_on_commit_failure(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'delete_trades_fail.db'}"
        setup_database(db_url=db_path)
        SQLTradeRepository().insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=5000.0,
            stop_loss=4900.0,
            take_profit=5200.0,
            risk=100.0,
            entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            trade_id="T1",
        )

        from src.infrastructure.database.database import db

        real_session = db.get_session()
        real_session.commit = MagicMock(side_effect=RuntimeError("boom"))
        real_session.rollback = MagicMock()
        fake_db = _FakeDB(real_session, db.get_engine())
        repo = SQLTradeRepository(db=fake_db)

        with pytest.raises(Exception) as exc_info:
            repo.delete_trades(["T1"])
        assert "boom" in str(exc_info.value)
        real_session.rollback.assert_called()
