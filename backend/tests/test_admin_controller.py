"""Tests for src/controllers/admin_controller.py."""

import os
import tempfile
from datetime import datetime

import pytest
from flask import Flask

from src.controllers.admin_controller import AdminController
from src.services.analytics_service import AnalyticsService
from tests.fakes import (
    FakeLineRepository,
    FakeLogger,
    FakeTradeRepository,
)


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
    return AdminController(analytics, line_repo, logger, log_dir="logs"), trade_repo, line_repo


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

    def test_get_trade_history_filters_by_account(self, controller):
        ctrl, repo, _ = controller
        from datetime import datetime, timezone
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100.0,
            stop_loss=90.0, take_profit=130.0, risk=10.0,
            entry_time=datetime.now(timezone.utc), account="Sim101",
        )
        repo.insert_trade(
            pair="MNQ", trade_type="short", entry_price=110.0,
            stop_loss=120.0, take_profit=80.0, risk=10.0,
            entry_time=datetime.now(timezone.utc), account="Live1",
        )
        resp, status = ctrl.get_trade_history("MNQ", limit=10, offset=0, account="Sim101")
        assert status == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert data["trades"][0]["account"] == "Sim101"

    def test_get_trade_accounts(self, controller):
        ctrl, repo, _ = controller
        from datetime import datetime, timezone
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100.0,
            stop_loss=90.0, take_profit=130.0, risk=10.0,
            entry_time=datetime.now(timezone.utc), account="Sim101",
        )
        repo.insert_trade(
            pair="MNQ", trade_type="short", entry_price=110.0,
            stop_loss=120.0, take_profit=80.0, risk=10.0,
            entry_time=datetime.now(timezone.utc), account="Live1",
        )
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=105.0,
            stop_loss=95.0, take_profit=135.0, risk=10.0,
            entry_time=datetime.now(timezone.utc),
        )
        resp, status = ctrl.get_trade_accounts("MNQ")
        assert status == 200
        data = resp.get_json()
        assert data["accounts"] == ["Live1", "Sim101"]


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


class TestAdminControllerDeleteTrades:

    def test_delete_trades_removes_requested_trades(self, controller):
        ctrl, repo, _ = controller
        from datetime import datetime, timezone
        t1 = repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100.0,
            stop_loss=90.0, take_profit=130.0, risk=10.0,
            entry_time=datetime.now(timezone.utc), trade_id="T1",
        )
        t2 = repo.insert_trade(
            pair="MNQ", trade_type="short", entry_price=110.0,
            stop_loss=120.0, take_profit=80.0, risk=10.0,
            entry_time=datetime.now(timezone.utc), trade_id="T2",
        )
        resp, status = ctrl.delete_trades([t1.trade_id, t2.trade_id])
        assert status == 200
        data = resp.get_json()
        assert data["deleted"] is True
        assert set(data["trade_ids"]) == {t1.trade_id, t2.trade_id}
        assert repo.get_trade(t1.trade_id) is None
        assert repo.get_trade(t2.trade_id) is None

    def test_delete_trades_not_found(self, controller):
        ctrl, _, _ = controller
        with pytest.raises(Exception):
            ctrl.delete_trades(["NONEXISTENT"])


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


class TestAdminControllerRecentLogs:

    def test_get_recent_logs_empty_when_file_missing(self, controller):
        ctrl, _, _ = controller
        # Point to a non-existent file by using a temp dir without logs
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            try:
                resp, status = ctrl.get_recent_logs("MNQ")
                assert status == 200
                data = resp.get_json()
                assert data["logs"] == []
                assert data["sources"] == []
                assert data["has_more"] is False
            finally:
                os.chdir(original_cwd)

    def test_get_recent_logs_parses_file(self, controller):
        ctrl, _, _ = controller
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            try:
                os.makedirs("logs", exist_ok=True)
                log_file = os.path.join("logs", f"app_{datetime.now().strftime('%Y-%m-%d')}.log")
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write("2024-06-21 10:30:15.123 [INFO] [LiveMode] SESSION END message\n")
                    f.write("2024-06-21 10:30:16.456 [WARN] [ZMQ] Broker fill handlers registered\n")
                    f.write("2024-06-21 10:30:17.789 [ERROR] Plain error without prefix\n")

                resp, status = ctrl.get_recent_logs("MNQ", limit=10, offset=0)
                assert status == 200
                data = resp.get_json()
                assert len(data["logs"]) == 3
                # Newest-first: last line written (ERROR) is returned first.
                assert data["logs"][0]["source"] == "server"
                assert data["logs"][1]["source"] == "ZMQ"
                assert data["logs"][2]["source"] == "LiveMode"
                assert set(data["sources"]) == {"LiveMode", "ZMQ", "server"}
                assert data["has_more"] is False
            finally:
                os.chdir(original_cwd)

    def test_get_recent_logs_pagination(self, controller):
        ctrl, _, _ = controller
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            try:
                os.makedirs("logs", exist_ok=True)
                log_file = os.path.join("logs", f"app_{datetime.now().strftime('%Y-%m-%d')}.log")
                with open(log_file, "w", encoding="utf-8") as f:
                    for i in range(5):
                        f.write(f"2024-06-21 10:30:{10 + i:02d}.000 [INFO] Message {i}\n")

                # Newest-first: Message 4 is the last line written and therefore most recent.
                resp, status = ctrl.get_recent_logs("MNQ", limit=2, offset=0)
                data = resp.get_json()
                assert len(data["logs"]) == 2
                assert data["logs"][0]["message"] == "Message 4"
                assert data["logs"][1]["message"] == "Message 3"
                assert data["has_more"] is True

                resp, status = ctrl.get_recent_logs("MNQ", limit=2, offset=2)
                data = resp.get_json()
                assert len(data["logs"]) == 2
                assert data["logs"][0]["message"] == "Message 2"
                assert data["logs"][1]["message"] == "Message 1"
            finally:
                os.chdir(original_cwd)

    def test_parse_log_line_with_instance_prefix(self, controller):
        ctrl, _, _ = controller
        entry = ctrl._parse_log_line(
            "2024-06-21 10:30:15.123 [worker] [INFO] [BrokerFill] Exit fill"
        )
        assert entry is not None
        assert entry["level"] == "INFO"
        assert entry["source"] == "BrokerFill"
        assert "Exit fill" in entry["message"]

    def test_get_recent_logs_uses_custom_log_dir(self, app_context):
        from pathlib import Path
        log_file = Path("logs/ninja") / f"app_{datetime.now().strftime('%Y-%m-%d')}.log"
        if not log_file.exists():
            pytest.skip("logs/ninja/app_YYYY-MM-DD.log not present")

        trade_repo = FakeTradeRepository()
        line_repo = FakeLineRepository()
        logger = FakeLogger()
        analytics = AnalyticsService(trade_repo)
        ctrl = AdminController(analytics, line_repo, logger, log_dir="logs/ninja")

        resp, status = ctrl.get_recent_logs("MNQ", limit=1, offset=0)
        assert status == 200
        data = resp.get_json()
        assert len(data["logs"]) == 1
        assert "level" in data["logs"][0]
        assert "source" in data["logs"][0]
