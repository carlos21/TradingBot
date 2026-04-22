"""Tests for AdminController decision log endpoints."""

import pytest
from flask import Flask

from src.controllers.admin_controller import AdminController
from src.services.analytics_service import AnalyticsService
from tests.fakes import FakeLineRepository, FakeLogger, FakeTradeRepository
from src.database.database import setup_database
from src.repositories.decision_log_repository import DecisionLogRepository


@pytest.fixture
def admin_ctrl(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'admin_test.db'}"
    setup_database(db_url=db_path)
    decision_repo = DecisionLogRepository()
    trade_repo = FakeTradeRepository()
    analytics = AnalyticsService(trade_repo)
    lines_repo = FakeLineRepository()
    logger = FakeLogger()
    ctrl = AdminController(analytics, lines_repo, logger, decision_repo)
    return ctrl, decision_repo


@pytest.fixture
def app_context():
    app = Flask(__name__)
    with app.app_context():
        yield


class TestDecisionLogs:

    def test_get_decision_logs_empty(self, admin_ctrl, app_context):
        ctrl, _ = admin_ctrl
        resp, status = ctrl.get_decision_logs("NQ")
        assert status == 200
        data = resp.get_json()
        assert data["logs"] == []
        assert "events" in data

    def test_get_decision_logs_with_filters(self, admin_ctrl, app_context):
        ctrl, repo = admin_ctrl
        repo.add_log(bar_time=1000.0, pair="NQ", event="LATCH", line_id="L1")
        repo.add_log(bar_time=2000.0, pair="NQ", event="FILTER_BLOCK", line_id="L1", filter_name="min_cross_depth")
        repo.add_log(bar_time=3000.0, pair="ES", event="LATCH", line_id="L2")

        # Filter by pair
        resp, _ = ctrl.get_decision_logs("NQ")
        data = resp.get_json()
        assert len(data["logs"]) == 2

        # Filter by event
        resp, _ = ctrl.get_decision_logs("NQ", event="FILTER_BLOCK")
        data = resp.get_json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["event"] == "FILTER_BLOCK"

        # Filter by line_id
        resp, _ = ctrl.get_decision_logs("NQ", line_id="L1")
        data = resp.get_json()
        assert len(data["logs"]) == 2

    def test_get_decision_events(self, admin_ctrl, app_context):
        ctrl, _ = admin_ctrl
        resp, status = ctrl.get_decision_events()
        assert status == 200
        data = resp.get_json()
        assert "events" in data
        assert "LATCH" in data["events"]
        assert "FILTER_BLOCK" in data["events"]
