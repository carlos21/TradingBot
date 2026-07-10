"""Tests for src/routes/admin_routes.py."""

import os

import pytest
from flask import Flask

from src.routes.admin_routes import register_admin_routes
from tests.fakes import FakeLogger


class FakeAdminController:
    def get_dashboard_stats(self, pair):
        return {"pair": pair, "trades": 0}

    def get_trade_history(self, pair, limit, offset, account=None):
        return {"trades": [], "total": 0, "account": account}

    def get_trade_accounts(self, pair):
        return {"accounts": ["Sim101"]}

    def get_trade_details(self, trade_id):
        return {"trade_id": trade_id}

    def delete_trade(self, trade_id):
        return {"deleted": True, "trade_id": trade_id}

    def delete_trades(self, trade_ids):
        return {"deleted": True, "trade_ids": trade_ids}

    def get_analytics(self, pair):
        return {"pair": pair}

    def get_decision_logs(self, pair, event, line_id, limit):
        return {"logs": []}

    def get_decision_events(self):
        return {"events": []}

    def get_recent_logs(self, pair, limit=200, offset=0):
        return {"logs": [], "sources": [], "has_more": False}


@pytest.fixture
def app():
    app = Flask(__name__, static_folder=None)
    app.config["TESTING"] = True
    ctrl = FakeAdminController()
    logger = FakeLogger()
    frontend_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "frontend",
    )
    register_admin_routes(app, ctrl, logger, frontend_dir=frontend_dir)
    return app


class TestAdminRoutes:

    def test_admin_redirects(self, app):
        with app.test_client() as client:
            resp = client.get("/admin")
            assert resp.status_code == 302  # Redirect to /admin/overview

    def test_admin_overview_serves_static_html(self, app):
        with app.test_client() as client:
            resp = client.get("/admin/overview")
            assert resp.status_code == 200
            assert resp.content_type.startswith("text/html")

    def test_admin_unknown_page_returns_404(self, app):
        with app.test_client() as client:
            resp = client.get("/admin/unknown")
            assert resp.status_code == 404

    def test_admin_stats(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/stats?pair=MNQ")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["pair"] == "MNQ"

    def test_admin_trades(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/trades?pair=MNQ")
            assert resp.status_code == 200

    def test_admin_trades_with_account_filter(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/trades?pair=MNQ&account=Sim101")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["account"] == "Sim101"

    def test_admin_trade_accounts(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/trade-accounts?pair=MNQ")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "accounts" in data

    def test_admin_trade_detail(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/trades/T1")
            assert resp.status_code == 200

    def test_admin_delete_trade(self, app):
        with app.test_client() as client:
            resp = client.delete("/api/admin/trades/T1")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["deleted"] is True
            assert data["trade_id"] == "T1"

    def test_admin_bulk_delete_trades(self, app):
        with app.test_client() as client:
            resp = client.post(
                "/api/admin/trades/bulk-delete",
                json={"trade_ids": ["T1", "T2"]},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["deleted"] is True
            assert data["trade_ids"] == ["T1", "T2"]

    def test_admin_bulk_delete_trades_requires_list(self, app):
        with app.test_client() as client:
            resp = client.post(
                "/api/admin/trades/bulk-delete",
                json={"trade_ids": []},
            )
            assert resp.status_code == 400

    def test_admin_bulk_delete_trades_rejects_invalid_ids(self, app):
        with app.test_client() as client:
            resp = client.post(
                "/api/admin/trades/bulk-delete",
                json={"trade_ids": ["T1", ""]},
            )
            assert resp.status_code == 400

    def test_admin_analytics(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/analytics?pair=MNQ")
            assert resp.status_code == 200

    def test_admin_decisions(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/decisions?pair=MNQ")
            assert resp.status_code == 200

    def test_admin_decision_events(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/decisions/events")
            assert resp.status_code == 200

    def test_admin_recent_logs(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/logs/recent?pair=MNQ")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "logs" in data
            assert "sources" in data
            assert "has_more" in data

    def test_admin_recent_logs_requires_pair(self, app):
        with app.test_client() as client:
            resp = client.get("/api/admin/logs/recent")
            assert resp.status_code == 400
