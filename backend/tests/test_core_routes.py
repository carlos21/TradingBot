"""Tests for src/routes/core_routes.py."""

import os

import pytest
from flask import Flask

from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.routes.core_routes import register_core_routes
from tests.fakes import FakeLogger


class GoodDataSource(CombinedDataSource):
    last_pair = None

    def load_historical_bars(self, timeframe="1m", start_time=None, end_time=None, pair=None):
        GoodDataSource.last_pair = pair
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


class FakeSettingsService:
    def get_instruments(self):
        return [
            {"symbol": "MNQ", "full_name": "MNQ 09-26", "point_value": 2.0, "session_start": "08:00", "session_end": "16:58", "daily_trades_limit": 1}
        ]


class BrokenSettingsService:
    """Simulates a settings store that is unavailable (e.g. no DB)."""

    def get_instruments(self):
        raise RuntimeError("Database not initialized. Call setup_database() first.")


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
        settings_service=FakeSettingsService(),
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
            assert "pair" not in data  # no default instrument anymore
            assert data["instruments"] == [
                {"symbol": "MNQ", "full_name": "MNQ 09-26", "point_value": 2.0, "session_start": "08:00", "session_end": "16:58", "daily_trades_limit": 1}
            ]
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

    def test_get_config_falls_back_when_settings_unavailable(self):
        # The in-memory scenario server runs without a DB; /api/config must
        # still respond so the frontend can load (otherwise stream_end never
        # arrives).
        app = Flask(__name__, static_folder=None)
        app.config["TESTING"] = True
        register_core_routes(
            app,
            pair="MNQ",
            data_source=GoodDataSource(),
            logger=FakeLogger(),
            frontend_dir="",
            platform_type="ninjatrader",
            platform_label="NinjaTrader",
            settings_service=BrokenSettingsService(),
        )
        with app.test_client() as client:
            resp = client.get("/api/config")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["instruments"] == []
            assert "pair" not in data

    def test_get_bars(self, app):
        with app.test_client() as client:
            resp = client.get("/api/bars?tf=1m&pair=MNQ")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 1
            assert data[0]["open"] == 100.0

    def test_get_bars_requires_pair(self, app):
        # There is no default instrument: /api/bars without a pair is a 400.
        with app.test_client() as client:
            resp = client.get("/api/bars?tf=1m")
            assert resp.status_code == 400

    def test_get_bars_with_start_time(self, app):
        with app.test_client() as client:
            resp = client.get("/api/bars?tf=1m&pair=MNQ&start_time=500")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 1

    def test_get_bars_forwards_pair_param(self, app):
        with app.test_client() as client:
            resp = client.get("/api/bars?tf=1m&pair=MES")
            assert resp.status_code == 200
        # GoodDataSource records the last pair kwarg it was called with.
        assert GoodDataSource.last_pair == "MES"
