"""Tests for src/database/unit_of_work.py."""

from datetime import datetime, timezone

import pytest

from src.dbexception import DBNotFoundException
from src.infrastructure.database.database import setup_database
from src.infrastructure.database.unit_of_work import UnitOfWork


@pytest.fixture
def uow_fixture(tmp_path):
    """Create a UnitOfWork backed by a temporary SQLite DB."""
    db_path = f"sqlite:///{tmp_path / 'uow_test.db'}"
    setup_database(db_url=db_path)
    return UnitOfWork


class TestUnitOfWorkContextManager:

    def test_commit_on_success(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            line = uow.lines.insert_line(pair="MNQ", price=5000.0)
            assert line.line_id is not None
            assert line.pair == "MNQ"
            assert line.price == 5000.0

        # After context exit, data should be committed
        with UoW() as uow:
            lines = uow.lines.list_lines("MNQ")
            assert len(lines) == 1
            assert lines[0].price == 5000.0

    def test_rollback_on_exception(self, uow_fixture):
        UoW = uow_fixture
        try:
            with UoW() as uow:
                uow.lines.insert_line(pair="MNQ", price=5000.0)
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # Data should be rolled back
        with UoW() as uow:
            lines = uow.lines.list_lines("MNQ")
            assert len(lines) == 0

    def test_nested_operations(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            line = uow.lines.insert_line(pair="MNQ", price=5000.0)
            trade = uow.trades.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=5000.0,
                stop_loss=4900.0,
                take_profit=5200.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            assert line.line_id is not None
            assert trade.trade_id is not None

        with UoW() as uow:
            assert len(uow.lines.list_lines("MNQ")) == 1
            assert len(uow.trades.list_trades("MNQ")) == 1


class TestUnitOfWorkLineRepository:

    def test_insert_and_get(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            line = uow.lines.insert_line(pair="MNQ", price=5000.0)
            fetched = uow.lines.get_line(line.line_id)
            assert fetched is not None
            assert fetched.price == 5000.0

    def test_get_missing(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            assert uow.lines.get_line("NONEXISTENT") is None

    def test_update(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            line = uow.lines.insert_line(pair="MNQ", price=5000.0)
            updated = uow.lines.update_line(line.line_id, 5100.0)
            assert updated.price == 5100.0

    def test_update_missing(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow, pytest.raises(DBNotFoundException):
            uow.lines.update_line("NONEXISTENT", 5100.0)

    def test_delete(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            line = uow.lines.insert_line(pair="MNQ", price=5000.0)
            uow.lines.delete_line(line.line_id)
            assert uow.lines.get_line(line.line_id) is None

    def test_list_lines(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            uow.lines.insert_line(pair="MNQ", price=5000.0)
            uow.lines.insert_line(pair="MNQ", price=5100.0)
            uow.lines.insert_line(pair="ES", price=4000.0)
            lines = uow.lines.list_lines("MNQ")
            assert len(lines) == 2


class TestUnitOfWorkTradeRepository:

    def test_insert_and_get(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            trade = uow.trades.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=5000.0,
                stop_loss=4900.0,
                take_profit=5200.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            fetched = uow.trades.get_trade(trade.trade_id)
            assert fetched is not None
            assert fetched.entry_price == 5000.0

    def test_close_trade(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            trade = uow.trades.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=5000.0,
                stop_loss=4900.0,
                take_profit=5200.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            closed = uow.trades.close_trade(
                trade_id=trade.trade_id,
                exit_price=5200.0,
                exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
                result=2.0,
                result_type="TP",
            )
            assert closed.exit_price == 5200.0
            assert closed.result == 2.0
            assert closed.result_type == "TP"

    def test_update_stop_loss(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            trade = uow.trades.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=5000.0,
                stop_loss=4900.0,
                take_profit=5200.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            updated = uow.trades.update_stop_loss(trade.trade_id, 4950.0)
            assert updated.stop_loss == 4950.0

    def test_update_take_profit(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            trade = uow.trades.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=5000.0,
                stop_loss=4900.0,
                take_profit=5200.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            updated = uow.trades.update_take_profit(trade.trade_id, 5300.0)
            assert updated.take_profit == 5300.0

    def test_update_entry_price(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            trade = uow.trades.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=5000.0,
                stop_loss=4900.0,
                take_profit=5200.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            updated = uow.trades.update_entry_price(trade.trade_id, 5010.0)
            assert updated.entry_price == 5010.0

    def test_update_risk_fields(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            trade = uow.trades.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=5000.0,
                stop_loss=4900.0,
                take_profit=5200.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            updated = uow.trades.update_risk_fields(trade.trade_id, 150.0, 300.0, 0.3)
            assert updated.risk == 150.0
            assert updated.risk_dollars == 300.0
            assert updated.risk_pct == 0.3

    def test_update_contracts(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            trade = uow.trades.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=5000.0,
                stop_loss=4900.0,
                take_profit=5200.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            updated = uow.trades.update_contracts(trade.trade_id, 2.0)
            assert updated.contracts == 2.0

    def test_list_trades(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            uow.trades.insert_trade(
                pair="MNQ",
                trade_type="long",
                entry_price=5000.0,
                stop_loss=4900.0,
                take_profit=5200.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            uow.trades.insert_trade(
                pair="ES",
                trade_type="short",
                entry_price=4000.0,
                stop_loss=4100.0,
                take_profit=3800.0,
                risk=100.0,
                entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
            trades = uow.trades.list_trades("MNQ")
            assert len(trades) == 1
            assert trades[0].pair == "MNQ"


class TestUnitOfWorkTriggerStateRepository:

    def test_save_and_load(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            uow.trigger_state.save("L1", "MNQ", {"crosses": 2, "latched": True})
            state = uow.trigger_state.load("L1")
            assert state == {"crosses": 2, "latched": True}

    def test_load_missing(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            assert uow.trigger_state.load("NONEXISTENT") is None

    def test_load_all(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            uow.trigger_state.save("L1", "MNQ", {"crosses": 2})
            uow.trigger_state.save("L2", "MNQ", {"crosses": 1})
            uow.trigger_state.save("L3", "ES", {"crosses": 3})
            states = uow.trigger_state.load_all("MNQ")
            assert len(states) == 2
            assert "L1" in states
            assert "L2" in states

    def test_delete(self, uow_fixture):
        UoW = uow_fixture
        with UoW() as uow:
            uow.trigger_state.save("L1", "MNQ", {"crosses": 2})
            uow.trigger_state.delete("L1")
            assert uow.trigger_state.load("L1") is None


class TestUnitOfWorkManualControl:

    def test_manual_commit(self, uow_fixture):
        UoW = uow_fixture
        uow = UoW()
        uow.lines.insert_line(pair="MNQ", price=5000.0)
        uow.commit()
        uow.close()

        with UoW() as uow2:
            lines = uow2.lines.list_lines("MNQ")
            assert len(lines) == 1

    def test_manual_rollback(self, uow_fixture):
        UoW = uow_fixture
        uow = UoW()
        uow.lines.insert_line(pair="MNQ", price=5000.0)
        uow.rollback()
        uow.close()

        with UoW() as uow2:
            lines = uow2.lines.list_lines("MNQ")
            assert len(lines) == 0
