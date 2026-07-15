"""Tests for TradesController — especially close_all_trades reuse of close_trade path."""

from unittest.mock import MagicMock

import pytest
from flask import Flask

from src.controllers.trades_controller import TradesController
from tests.fakes import (
    DummySocketIO,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


class FakeBarsLoader:
    """Minimal fake for controller tests."""

    def __init__(self, close_price=100.0):
        if close_price is None:
            self._1m_buffer = []
            self._last_bar_close = 0
            self.data_source = self
        else:
            self._1m_buffer = [{"close": close_price, "time": 1000}]
            self._last_bar_close = close_price
            self.data_source = self  # self-serve for load_historical_bars
        self._last_played_ts = 0
        self.current_1m_index = {"MNQ": 0}

    def load_historical_bars(self, _tf):
        if not self._1m_buffer:
            return []
        return [{"close": self._last_bar_close, "time": 1000}]


@pytest.fixture
def app_context():
    app = Flask(__name__)
    with app.app_context():
        yield


def _make_controller(close_price=100.0, rr_ratio=5.0, **tm_overrides):
    repo = FakeTradeRepository()
    executor = FakeTradeExecutor()
    socketio = DummySocketIO()
    logger = FakeLogger()
    loader = FakeBarsLoader(close_price=close_price)

    from src.services.trade_manager import TradeManager
    tm = TradeManager(
        trade_repository=repo,
        socketio=socketio,
        pair="MNQ",
        trade_executor=executor,
        point_value=2.0,
        account_balance=100000.0,
        logger=logger,
        **tm_overrides,
    )
    controller = TradesController(loader, tm, logger=logger, rr_ratio=rr_ratio)
    return controller, tm, executor, repo


class TestListTrades:

    def test_list_trades_returns_empty_when_none(self, app_context):
        controller, tm, executor, repo = _make_controller()
        resp, status = controller.list_trades("MNQ")
        assert status == 200
        assert resp.get_json() == []

    def test_list_trades_returns_open_and_closed(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)
        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = tm.open_trades[0]["trade_id"]
        tm.close_trade(trade_id, 105.0, 600.0)

        resp, status = controller.list_trades("MNQ")
        assert status == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["trade_id"] == trade_id
        assert data[0]["status"] == "closed"
        assert data[0]["exit_price"] == 105.0

    def test_list_trades_filters_by_pair(self, app_context):
        controller, tm, executor, repo = _make_controller()
        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        tm.open_trade("ES", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)

        resp, status = controller.list_trades("MNQ")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["pair"] == "MNQ"


class TestOpenTrade:

    def test_open_trade_long_success(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=100.0)
        resp, status = controller.open_trade("MNQ", 90.0, "long")
        assert status == 201
        data = resp.get_json()
        assert data["type"] == "long"
        assert data["stop_loss"] == 90.0
        assert data["take_profit"] == 150.0
        assert executor.opens

    def test_open_trade_short_success(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=100.0)
        resp, status = controller.open_trade("MNQ", 110.0, "short")
        assert status == 201
        data = resp.get_json()
        assert data["type"] == "short"
        assert data["take_profit"] == 50.0

    def test_open_trade_invalid_rr_ratio(self, app_context):
        controller, tm, executor, repo = _make_controller(rr_ratio=0)
        with pytest.raises(Exception) as exc_info:
            controller.open_trade("MNQ", 90.0, "long")
        assert exc_info.value.code == 400

    def test_open_trade_invalid_stop_loss(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=100.0)
        with pytest.raises(Exception) as exc_info:
            controller.open_trade("MNQ", 100.0, "long")
        assert exc_info.value.code == 400

    def test_open_trade_no_price_data(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=None)
        with pytest.raises(Exception) as exc_info:
            controller.open_trade("MNQ", 90.0, "long")
        assert exc_info.value.code == 400

    def test_open_trade_manager_exception_returns_500(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=100.0)
        tm.open_trade = MagicMock(side_effect=Exception("broker down"))
        with pytest.raises(Exception) as exc_info:
            controller.open_trade("MNQ", 90.0, "long")
        assert exc_info.value.code == 500


class TestOpenTestTrade:

    def test_open_test_trade_long(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=100.0)
        resp, status = controller.open_test_trade("MNQ", "long")
        assert status == 201
        data = resp.get_json()
        assert data["type"] == "long"
        assert data["stop_loss"] == 80.0
        assert data["take_profit"] == 200.0
        assert data["source"] == "test"

    def test_open_test_trade_short(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=100.0)
        resp, status = controller.open_test_trade("MNQ", "short")
        assert status == 201
        data = resp.get_json()
        assert data["type"] == "short"
        assert data["stop_loss"] == 120.0
        assert data["take_profit"] == 0.0

    def test_open_test_trade_no_price_data(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=None)
        with pytest.raises(Exception) as exc_info:
            controller.open_test_trade("MNQ", "long")
        assert exc_info.value.code == 400

    def test_open_test_trade_manager_exception_returns_500(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=100.0)
        tm.open_trade = MagicMock(side_effect=Exception("broker down"))
        with pytest.raises(Exception) as exc_info:
            controller.open_test_trade("MNQ", "long")
        assert exc_info.value.code == 500


class TestCloseAllTrades:

    def test_closes_all_open_trades_and_notifies_executor(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        # Seed two open trades
        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        tm.open_trade("MNQ", "short", 100.0, 110.0, 70.0, 10.0, 500.0, 5.0)

        assert len(tm.open_trades) == 2
        assert len(executor.closes) == 0

        resp, status = controller.close_all_trades("MNQ")

        assert status == 200
        data = resp.get_json()
        assert data["count"] == 2
        assert len(data["closed"]) == 2
        assert len(data["failed"]) == 0
        assert len(tm.open_trades) == 0
        assert len(repo.closed) == 2
        # Each close should have triggered the executor (ZMQ -> NinjaTrader)
        assert len(executor.closes) == 2

    def test_returns_empty_when_no_open_trades(self, app_context):
        controller, tm, executor, repo = _make_controller()

        resp, status = controller.close_all_trades("MNQ")

        assert status == 200
        data = resp.get_json()
        assert data["closed"] == []
        assert data["message"] == "No open trades to close"

    def test_only_closes_trades_for_requested_pair(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        tm.open_trade("ES", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)

        resp, status = controller.close_all_trades("MNQ")

        assert status == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert len(tm.open_trades) == 1
        assert tm.open_trades[0]["pair"] == "ES"

    def test_records_failed_closes(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)
        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)

        original_close = tm.close_trade
        call_count = [0]

        def flaky_close(trade_id, exit_price, exit_time):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("broker down")
            return original_close(trade_id, exit_price, exit_time)

        tm.close_trade = flaky_close

        resp, status = controller.close_all_trades("MNQ")
        assert status == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert len(data["closed"]) == 1
        assert len(data["failed"]) == 1
        assert data["failed"][0]["error"] == "broker down"


class TestControllerCloseTrade:
    """Tests for the single-trade close endpoint."""

    def test_close_trade_closes_and_returns_payload(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = tm.open_trades[0]["trade_id"]

        resp, status = controller.close_trade(trade_id)

        assert status == 200
        data = resp.get_json()
        assert data["trade_id"] == trade_id
        assert data["exit_price"] == 105.0
        assert len(tm.open_trades) == 0
        assert len(repo.closed) == 1

    def test_close_trade_not_found_returns_404(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        with pytest.raises(Exception) as exc_info:
            controller.close_trade("NONEXISTENT")
        assert exc_info.value.code == 404


class TestModifyStopLoss:
    """Tests for the stop-loss modification endpoint."""

    def test_modify_stop_loss_for_open_trade(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = tm.open_trades[0]["trade_id"]

        resp, status = controller.modify_stop_loss(trade_id, 88.0)

        assert status == 200
        data = resp.get_json()
        assert data["trade_id"] == trade_id
        assert data["stop_loss"] == 88.0
        assert tm.open_trades[0]["stop_loss"] == 88.0
        assert len(executor.sl_updates) == 1
        assert executor.sl_updates[0] == (trade_id, 88.0)

    def test_modify_stop_loss_falls_back_to_repository_trade(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        trade = tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = trade["trade_id"]
        # Simulate the trade no longer being in the in-memory open list
        tm.open_trades.clear()

        resp, status = controller.modify_stop_loss(trade_id, 87.0)

        assert status == 200
        data = resp.get_json()
        assert data["trade_id"] == trade_id
        assert data["stop_loss"] == 87.0
        assert repo.get_trade(trade_id).stop_loss == 87.0
        assert len(executor.sl_updates) == 1
        assert executor.sl_updates[0] == (trade_id, 87.0)

    def test_modify_stop_loss_not_found_returns_404(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        with pytest.raises(Exception) as exc_info:
            controller.modify_stop_loss("NONEXISTENT", 88.0)
        assert exc_info.value.code == 404

    def test_modify_stop_loss_closed_trade_returns_404(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = tm.open_trades[0]["trade_id"]
        tm.close_trade(trade_id, 95.0, 600.0)

        with pytest.raises(Exception) as exc_info:
            controller.modify_stop_loss(trade_id, 88.0)
        assert exc_info.value.code == 404

    def test_modify_stop_loss_invalid_value_returns_400(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = tm.open_trades[0]["trade_id"]

        with pytest.raises(Exception) as exc_info:
            controller.modify_stop_loss(trade_id, "not-a-number")
        assert exc_info.value.code == 400

    def test_modify_stop_loss_non_positive_returns_400(self, app_context):
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = tm.open_trades[0]["trade_id"]

        with pytest.raises(Exception) as exc_info:
            controller.modify_stop_loss(trade_id, 0)
        assert exc_info.value.code == 400
