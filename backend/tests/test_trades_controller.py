"""Tests for TradesController — especially close_all_trades reuse of close_trade path."""

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
        self._1m_buffer = [{"close": close_price, "time": 1000}]
        self._last_bar_close = close_price
        self._last_played_ts = 0
        self.current_1m_index = {"MNQ": 0}
        self.data_source = self  # self-serve for load_historical_bars

    def load_historical_bars(self, _tf):
        return [{"close": self._last_bar_close, "time": 1000}]


@pytest.fixture
def app_context():
    app = Flask(__name__)
    with app.app_context():
        yield


def _make_controller(close_price=100.0, **tm_overrides):
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
    controller = TradesController(loader, tm, logger=logger, rr_ratio=5.0)
    return controller, tm, executor, repo


class TestCloseAllTrades:

    def test_closes_all_open_trades_and_notifies_executor(self, app_context):  # noqa: ARG002
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

    def test_returns_empty_when_no_open_trades(self, app_context):  # noqa: ARG002
        controller, tm, executor, repo = _make_controller()

        resp, status = controller.close_all_trades("MNQ")

        assert status == 200
        data = resp.get_json()
        assert data["closed"] == []
        assert data["message"] == "No open trades to close"

    def test_only_closes_trades_for_requested_pair(self, app_context):  # noqa: ARG002
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        tm.open_trade("ES", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)

        resp, status = controller.close_all_trades("MNQ")

        assert status == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert len(tm.open_trades) == 1
        assert tm.open_trades[0]["pair"] == "ES"


class TestControllerCloseTrade:
    """Tests for the single-trade close endpoint."""

    def test_close_trade_closes_and_returns_payload(self, app_context):  # noqa: ARG002
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

    def test_close_trade_not_found_returns_404(self, app_context):  # noqa: ARG002
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        with pytest.raises(Exception) as exc_info:
            controller.close_trade("NONEXISTENT")
        assert exc_info.value.code == 404


class TestModifyStopLoss:
    """Tests for the stop-loss modification endpoint."""

    def test_modify_stop_loss_for_open_trade(self, app_context):  # noqa: ARG002
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

    def test_modify_stop_loss_falls_back_to_repository_trade(self, app_context):  # noqa: ARG002
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

    def test_modify_stop_loss_not_found_returns_404(self, app_context):  # noqa: ARG002
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        with pytest.raises(Exception) as exc_info:
            controller.modify_stop_loss("NONEXISTENT", 88.0)
        assert exc_info.value.code == 404

    def test_modify_stop_loss_closed_trade_returns_404(self, app_context):  # noqa: ARG002
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = tm.open_trades[0]["trade_id"]
        tm.close_trade(trade_id, 95.0, 600.0)

        with pytest.raises(Exception) as exc_info:
            controller.modify_stop_loss(trade_id, 88.0)
        assert exc_info.value.code == 404

    def test_modify_stop_loss_invalid_value_returns_400(self, app_context):  # noqa: ARG002
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = tm.open_trades[0]["trade_id"]

        with pytest.raises(Exception) as exc_info:
            controller.modify_stop_loss(trade_id, "not-a-number")
        assert exc_info.value.code == 400

    def test_modify_stop_loss_non_positive_returns_400(self, app_context):  # noqa: ARG002
        controller, tm, executor, repo = _make_controller(close_price=105.0)

        tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        trade_id = tm.open_trades[0]["trade_id"]

        with pytest.raises(Exception) as exc_info:
            controller.modify_stop_loss(trade_id, 0)
        assert exc_info.value.code == 400
