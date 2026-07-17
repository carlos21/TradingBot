"""E2E test: /api/lines endpoints are isolated by instrument pair.

Builds a minimal Flask app wired to a shared ``SQLLineRepository`` and two
per-pair ``LinesController`` instances.  The routes delegate to the controller
for the pair carried in the request, matching the production wiring where a
``StreamCoordinator`` provides per-instrument controllers.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from flask import Flask

from app_factory import Repositories
from src.application.streaming_session import StreamingSession
from src.domain.models import Instrument
from src.infrastructure.database.database_protocol import Base, get_database
from src.infrastructure.repositories.lines_repository import SQLLineRepository
from src.routes.lines_routes import register_lines_routes
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from tests.fakes import FakeDataSource, FakeLogger, FakeTradeRepository

os.environ.setdefault("SECRET_KEY", "test-secret")


class _FakeCoordinator:
    """Provides per-instrument controllers the same way StreamCoordinator does."""

    def __init__(self, controllers: dict[str, object]):
        self._controllers = controllers

    def require_session(self, pair: str):
        class _Ctx:
            pass

        ctx = _Ctx()
        ctx.lines_controller = self._controllers[pair]
        return ctx


@pytest.fixture
def lines_client():
    """Flask test client with per-pair controllers backed by one repository."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{path}"
    db = get_database(db_url)
    db.create_tables(Base)
    line_repo = SQLLineRepository(db=db)

    def _build_controller(symbol: str):
        instrument = Instrument(symbol=symbol, full_name=symbol, point_value=2.0)
        data_source = FakeDataSource(pair=symbol, bars=[])
        numbers = StrategyNumbers(
            min_stop_loss=10.0,
            max_bounce=90.0,
            extra_sl_space=0.0,
            fixed_stop_loss=20.0,
            rr_ratio=2.0,
            point_value=2.0,
            account_balance=100000.0,
        )
        session = StreamingSession(
            instrument=instrument,
            socketio=None,
            data_source=data_source,
            repos=Repositories(lines=line_repo, trades=FakeTradeRepository()),
            numbers=numbers,
            options=DEFAULT_STRATEGY_OPTIONS,
            candle_config=CandleConfig(),
            timeframes=["5m"],
            live_mode=False,
            logger=FakeLogger(),
            bootstrap_existing_lines=True,
        )
        return session.lines_controller

    controllers = {
        "MNQ": _build_controller("MNQ"),
        "ES": _build_controller("ES"),
    }

    app = Flask(__name__)
    app.config["TESTING"] = True
    register_lines_routes(app, lines_controller=controllers["MNQ"], _logger=FakeLogger(), coordinator=_FakeCoordinator(controllers))

    try:
        with app.test_client() as client:
            yield client
    finally:
        db.get_engine().dispose()
        os.unlink(path)


class TestLinePairScopingE2E:
    def test_post_line_is_isolated_by_pair(self, lines_client):
        resp_mnq = lines_client.post("/api/lines", json={"pair": "MNQ", "price": 22000.0})
        assert resp_mnq.status_code == 201
        assert resp_mnq.get_json()["pair"] == "MNQ"

        resp_es = lines_client.post("/api/lines", json={"pair": "ES", "price": 4500.0})
        assert resp_es.status_code == 201
        assert resp_es.get_json()["pair"] == "ES"

        mnq_list = lines_client.get("/api/lines?pair=MNQ").get_json()
        es_list = lines_client.get("/api/lines?pair=ES").get_json()

        assert len(mnq_list) == 1
        assert mnq_list[0]["pair"] == "MNQ"
        assert len(es_list) == 1
        assert es_list[0]["pair"] == "ES"

    def test_get_line_without_pair_returns_400(self, lines_client):
        resp = lines_client.get("/api/lines")
        assert resp.status_code == 400

    def test_post_line_rejects_missing_pair(self, lines_client):
        resp = lines_client.post("/api/lines", json={"price": 22000.0})
        assert resp.status_code == 400
