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

    def test_open_trade_invalid_pair_or_type(self, app):
        with app.test_client() as client:
            resp = client.post("/api/trades", json={"pair": "MNQ", "type": "invalid", "stop_loss": 90.0})
            assert resp.status_code == 400

    def test_open_trade_invalid_stop_loss(self, app):
        with app.test_client() as client:
            resp = client.post("/api/trades", json={"pair": "MNQ", "type": "long", "stop_loss": "bad"})
            assert resp.status_code == 400

    def test_open_test_trade_invalid_direction(self, app):
        with app.test_client() as client:
            resp = client.post("/api/trades/test", json={"pair": "MNQ", "direction": "sideways"})
            assert resp.status_code == 400

    def test_modify_stop_loss_invalid(self, app):
        with app.test_client() as client:
            resp = client.post("/api/trades/T1/stop-loss", json={"stop_loss": "bad"})
            assert resp.status_code == 400

    def test_close_all_trades_missing_pair(self, app):
        with app.test_client() as client:
            resp = client.post("/api/trades/close-all", json={})
            assert resp.status_code == 400

    def test_get_trade_logs_text_format(self, app, monkeypatch):
        from datetime import datetime
        from src.domain.models import TradeData

        def _fake_get(_self, _trade_id):
            return TradeData(
                trade_id="T1",
                pair="MNQ",
                trade_type="long",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=110.0,
                risk=1.0,
                risk_dollars=None,
                risk_pct=None,
                account_balance=None,
                contracts=None,
                entry_time=datetime.utcnow(),
                exit_price=None,
                exit_time=None,
                result=None,
                result_type=None,
                fees=None,
                pnl_usd=None,
                params=None,
                logs=[{"ts": datetime.utcnow().isoformat(), "event": "OPEN", "msg": "opened at 100"}],
            )

        monkeypatch.setattr(FakeTradeRepository, "get_trade", _fake_get)
        with app.test_client() as client:
            resp = client.get("/api/trades/T1/logs?format=text")
            assert resp.status_code == 200
            assert resp.content_type.startswith("text/plain")

    def test_get_trade_logs_text_format_not_found(self, app):
        with app.test_client() as client:
            resp = client.get("/api/trades/UNKNOWN/logs?format=text")
            assert resp.status_code == 404

    def test_get_trade_logs_json_format(self, app):
        with app.test_client() as client:
            resp = client.get("/api/trades/T1/logs")
            assert resp.status_code == 200

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
