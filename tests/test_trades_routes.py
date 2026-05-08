"""Tests for src/routes/trades_routes.py."""

import pytest
from flask import Flask

from src.routes.trades_routes import register_trades_routes
from tests.fakes import FakeLogger, FakeTradeRepository


class FakeTradesController:
    def __init__(self):
        self.repo = FakeTradeRepository()

    def list_trades(self, pair):
        trades = self.repo.list_trades(pair)
        return {"trades": [], "total": len(trades)}

    def open_trade(self, pair, stop_loss, trade_type):
        return {"trade_id": "T1"}, 201

    def open_test_trade(self, pair, direction):
        return {"trade_id": "T1"}, 201

    def close_trade(self, trade_id):
        return {"closed": trade_id}, 200

    def close_all_trades(self, pair):
        return {"count": 0}, 200


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    ctrl = FakeTradesController()
    repo = FakeTradeRepository()
    logger = FakeLogger()
    register_trades_routes(app, ctrl, repo, "MNQ", None, logger)
    return app


class TestTradesRoutes:

    def test_list_trades(self, app):
        with app.test_client() as client:
            resp = client.get("/api/trades?pair=MNQ")
            assert resp.status_code == 200

    def test_list_trades_missing_pair(self, app):
        with app.test_client() as client:
            resp = client.get("/api/trades")
            assert resp.status_code == 400

    def test_open_trade(self, app):
        with app.test_client() as client:
            resp = client.post("/api/trades", json={"pair": "MNQ", "type": "long", "stop_loss": 90.0})
            assert resp.status_code == 201

    def test_open_test_trade(self, app):
        with app.test_client() as client:
            resp = client.post("/api/trades/test", json={"pair": "MNQ", "direction": "long"})
            assert resp.status_code == 201

    def test_close_trade(self, app):
        with app.test_client() as client:
            resp = client.post("/api/trades/T1/close")
            assert resp.status_code == 200

    def test_close_all_trades(self, app):
        with app.test_client() as client:
            resp = client.post("/api/trades/close-all", json={"pair": "MNQ"})
            assert resp.status_code == 200

    def test_get_trade_logs(self, app):
        with app.test_client() as client:
            resp = client.get("/api/trades/T1/logs")
            assert resp.status_code == 200
