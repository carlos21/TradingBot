"""Tests for src/routes/core_routes.py."""

import os

import pytest
from flask import Flask

from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.routes.core_routes import register_core_routes
from tests.fakes import FakeLogger


class GoodDataSource(CombinedDataSource):
    def load_historical_bars(self, timeframe="1m", start_time=None):
        bars = [
            {"time": 1000, "open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0, "volume": 100, "pair": "MNQ"},
        ]
        if start_time is not None:
            bars = [b for b in bars if b["time"] >= start_time]
        return bars
    def subscribe(self, callback, from_time=0):
        pass
    def pause(self):
        pass


@pytest.fixture
def app():
    app = Flask(__name__, static_folder=None)
    app.config["TESTING"] = True
    ds = GoodDataSource()
    logger = FakeLogger()
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    frontend_dir = os.path.join(repo_root, "frontend")
    register_core_routes(
        app,
        pair="MNQ",
        data_source=ds,
        logger=logger,
        frontend_dir=frontend_dir,
        platform_type="ninjatrader",
        platform_label="NinjaTrader",
    )
    return app


class TestCoreRoutes:

    def test_index_serves_static_html(self, app):
        with app.test_client() as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert resp.content_type.startswith("text/html")

    def test_get_config(self, app):
        with app.test_client() as client:
            resp = client.get("/api/config")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["pair"] == "MNQ"
            assert data["platform_type"] == "ninjatrader"
            assert data["platform_label"] == "NinjaTrader"
            assert data["is_ninjatrader"] is True
            assert data["is_metatrader"] is False

    def test_get_pair(self, app):
        with app.test_client() as client:
            resp = client.get("/api/pair")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["pair"] == "MNQ"

    def test_get_bars(self, app):
        with app.test_client() as client:
            resp = client.get("/api/bars?tf=1m")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 1
            assert data[0]["open"] == 100.0

    def test_get_bars_with_start_time(self, app):
        with app.test_client() as client:
            resp = client.get("/api/bars?tf=1m&start_time=500")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 1
