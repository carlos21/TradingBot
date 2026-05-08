"""Tests for src/routes/debug_routes.py."""

import pytest
from flask import Flask

from src.routes.debug_routes import register_debug_routes
from tests.fakes import (
    FakeAnalyticsReporter,
    FakeDataSource,
    FakeLineRepository,
    FakeLogger,
    FakeNotifier,
    FakeTradeRepository,
    DummySocketIO,
)


class FakeStrategy:
    def __init__(self):
        self.decision_logs = [{"time": 1, "msg": "test"}]
        self._reset_called = False

    def reset(self):
        self._reset_called = True
        self.decision_logs.clear()

    def on_raw_bar(self, bar):
        pass


class FakeLoader:
    def __init__(self):
        self._reset_called = False

    def reset(self):
        self._reset_called = True


class FakeTradeManager:
    def __init__(self):
        self.open_trades = {"T1": "trade1"}
        self._monitored_trades = {"T1": "monitor1"}


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def setup_debug_routes(app):
    strategy = FakeStrategy()
    loader = FakeLoader()
    trade_manager = FakeTradeManager()
    lines_repo = FakeLineRepository()
    trades_repo = FakeTradeRepository()
    data_source = FakeDataSource(bars=[{"time": 1, "open": 100}])
    notifier = FakeNotifier()
    analytics = FakeAnalyticsReporter()
    logger = FakeLogger()

    register_debug_routes(
        app=app,
        strategy=strategy,
        loader=loader,
        trade_manager=trade_manager,
        lines_repo=lines_repo,
        trades_repo=trades_repo,
        data_source=data_source,
        pair="MNQ",
        notifier=notifier,
        analytics=analytics,
        logger=logger,
    )

    return {
        "strategy": strategy,
        "loader": loader,
        "trade_manager": trade_manager,
        "lines_repo": lines_repo,
        "trades_repo": trades_repo,
        "data_source": data_source,
        "notifier": notifier,
        "analytics": analytics,
        "logger": logger,
    }


class TestDebugLogs:

    def test_get_debug_logs(self, app, setup_debug_routes):
        with app.test_client() as client:
            resp = client.get("/api/debug/logs")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data == [{"time": 1, "msg": "test"}]

    def test_get_debug_logs_empty(self, app, setup_debug_routes):
        setup_debug_routes["strategy"].decision_logs.clear()
        with app.test_client() as client:
            resp = client.get("/api/debug/logs")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data == []


class TestResetAll:

    def test_reset_all_basic(self, app, setup_debug_routes):
        with app.test_client() as client:
            resp = client.post("/__reset_all", json={})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "OK"

    def test_reset_all_clears_strategy(self, app, setup_debug_routes):
        with app.test_client() as client:
            client.post("/__reset_all", json={})
            assert setup_debug_routes["strategy"]._reset_called is True

    def test_reset_all_clears_loader(self, app, setup_debug_routes):
        with app.test_client() as client:
            client.post("/__reset_all", json={})
            assert setup_debug_routes["loader"]._reset_called is True

    def test_reset_all_clears_trade_manager(self, app, setup_debug_routes):
        with app.test_client() as client:
            client.post("/__reset_all", json={})
            assert setup_debug_routes["trade_manager"].open_trades == {}
            assert setup_debug_routes["trade_manager"]._monitored_trades == {}

    def test_reset_all_clears_trades_repo(self, app, setup_debug_routes):
        setup_debug_routes["trades_repo"].insert_trade(
            pair="MNQ", trade_type="buy", entry_price=100,
            stop_loss=90, take_profit=120, risk=1.0,
            entry_time=1
        )
        with app.test_client() as client:
            client.post("/__reset_all", json={})
            assert setup_debug_routes["trades_repo"].inserted == []

    def test_reset_all_with_time_range(self, app, setup_debug_routes):
        data_source = setup_debug_routes["data_source"]
        with app.test_client() as client:
            resp = client.post(
                "/__reset_all",
                json={"start_time": 1000, "end_time": 2000},
            )
            assert resp.status_code == 200

    def test_reset_all_lines_repo_error(self, app, setup_debug_routes):
        class BrokenLineRepo(FakeLineRepository):
            def list_lines(self, pair):
                raise RuntimeError("db down")

        broken_repo = BrokenLineRepo()
        setup_debug_routes["lines_repo"] = broken_repo

        # Re-register routes with broken repo
        app_copy = Flask(__name__)
        app_copy.config["TESTING"] = True
        register_debug_routes(
            app=app_copy,
            strategy=setup_debug_routes["strategy"],
            loader=setup_debug_routes["loader"],
            trade_manager=setup_debug_routes["trade_manager"],
            lines_repo=broken_repo,
            trades_repo=setup_debug_routes["trades_repo"],
            data_source=setup_debug_routes["data_source"],
            pair="MNQ",
            notifier=setup_debug_routes["notifier"],
            analytics=setup_debug_routes["analytics"],
            logger=setup_debug_routes["logger"],
        )

        with app_copy.test_client() as client:
            resp = client.post("/__reset_all", json={})
            assert resp.status_code == 500
            data = resp.get_json()
            assert "db down" in data["error"]

    def test_reset_all_critical_error(self, app, setup_debug_routes):
        class BrokenStrategy:
            decision_logs = []

            def reset(self):
                raise RuntimeError("boom")

        broken_strategy = BrokenStrategy()
        app_copy = Flask(__name__)
        app_copy.config["TESTING"] = True
        register_debug_routes(
            app=app_copy,
            strategy=broken_strategy,
            loader=setup_debug_routes["loader"],
            trade_manager=setup_debug_routes["trade_manager"],
            lines_repo=setup_debug_routes["lines_repo"],
            trades_repo=setup_debug_routes["trades_repo"],
            data_source=setup_debug_routes["data_source"],
            pair="MNQ",
            notifier=setup_debug_routes["notifier"],
            analytics=setup_debug_routes["analytics"],
            logger=setup_debug_routes["logger"],
        )

        with app_copy.test_client() as client:
            resp = client.post("/__reset_all", json={})
            assert resp.status_code == 500
            data = resp.get_json()
            assert "boom" in data["error"]


class TestShutdown:

    def test_shutdown_no_werkzeug(self, app, setup_debug_routes):
        with app.test_client() as client:
            resp = client.post("/__shutdown")
            assert resp.status_code == 200
            assert resp.data == b"OK"
