"""Tests for src/controllers/admin_controller.py."""

import pytest
from flask import Flask

from src.controllers.admin_controller import AdminController
from tests.fakes import (
    FakeLineRepository,
    FakeLogger,
    FakeTradeRepository,
)
from src.services.analytics_service import AnalyticsService


@pytest.fixture
def app_context():
    app = Flask(__name__)
    with app.app_context():
        yield


@pytest.fixture
def controller(app_context):
    trade_repo = FakeTradeRepository()
    line_repo = FakeLineRepository()
    logger = FakeLogger()
    analytics = AnalyticsService(trade_repo)
    return AdminController(analytics, line_repo, logger), trade_repo, line_repo


class TestAdminControllerDashboardStats:

    def test_get_dashboard_stats(self, controller):
        ctrl, repo, _ = controller
        from datetime import datetime, timezone
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100.0,
            stop_loss=90.0, take_profit=130.0, risk=10.0,
            entry_time=datetime.now(timezone.utc),
        )
        resp, status = ctrl.get_dashboard_stats("MNQ")
        assert status == 200
        data = resp.get_json()
        assert data["total_trades"] == 1


class TestAdminControllerTradeHistory:

    def test_get_trade_history(self, controller):
        ctrl, _, _ = controller
        resp, status = ctrl.get_trade_history("MNQ", limit=10, offset=0)
        assert status == 200
        data = resp.get_json()
        assert "trades" in data
        assert data["total"] == 0


class TestAdminControllerTradeDetails:

    def test_get_trade_details_not_found(self, controller):
        ctrl, _, _ = controller
        with pytest.raises(Exception):
            ctrl.get_trade_details("NONEXISTENT")

    def test_get_trade_details_found(self, controller):
        ctrl, repo, _ = controller
        from datetime import datetime, timezone
        trade = repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100.0,
            stop_loss=90.0, take_profit=130.0, risk=10.0,
            entry_time=datetime.now(timezone.utc),
        )
        resp, status = ctrl.get_trade_details(trade.trade_id)
        assert status == 200
        data = resp.get_json()
        assert data["trade_id"] == trade.trade_id


class TestAdminControllerAnalytics:

    def test_get_analytics(self, controller):
        ctrl, _, _ = controller
        resp, status = ctrl.get_analytics("MNQ")
        assert status == 200
        data = resp.get_json()
        assert "equity_curve" in data
        assert "trades_by_hour" in data
        assert "trades_by_day" in data
        assert "result_distribution" in data
        assert "monthly_pnl" in data
        assert "pnl_distribution" in data


class TestAdminControllerLines:

    def test_get_lines_empty(self, controller):
        ctrl, _, _ = controller
        resp, status = ctrl.get_lines("MNQ")
        assert status == 200
        data = resp.get_json()
        assert data == []

    def test_get_lines_with_data(self, controller):
        ctrl, _, line_repo = controller
        line_repo.insert_line("MNQ", 5000.0)
        resp, status = ctrl.get_lines("MNQ")
        assert status == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["pair"] == "MNQ"
        assert data[0]["price"] == 5000.0


class TestAdminControllerDecisionLogs:

    def test_get_decision_logs_no_repo(self, controller):
        ctrl, _, _ = controller
        resp, status = ctrl.get_decision_logs("MNQ")
        assert status == 200
        data = resp.get_json()
        assert data["logs"] == []
        assert "events" in data

    def test_get_decision_events(self, controller):
        ctrl, _, _ = controller
        resp, status = ctrl.get_decision_events()
        assert status == 200
        data = resp.get_json()
        assert "events" in data
        assert "LATCH" in data["events"]
