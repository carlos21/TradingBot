import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_CHAT_ID", "dummy")

import pytest

from src.infrastructure.database.database_protocol import Base, get_database
from src.infrastructure.database.database import setup_database, db as global_db
from src.services.trade_logger import TradeLogger
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_v2.base_strategy import StrategyOptions
from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLineRepository,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


@pytest.fixture
def socketio():
    return DummySocketIO()


@pytest.fixture
def line_repo():
    return FakeLineRepository()


@pytest.fixture
def trade_repo():
    return FakeTradeRepository()


@pytest.fixture
def trade_executor():
    return FakeTradeExecutor()


@pytest.fixture
def trade_logger(trade_repo):
    return TradeLogger(trade_repo)


@pytest.fixture
def analytics():
    return FakeAnalyticsReporter()


@pytest.fixture
def logger():
    return FakeLogger()


@pytest.fixture
def trade_manager(trade_repo, socketio, trade_executor, analytics, logger):
    return TradeManager(
        trade_repository=trade_repo,
        socketio=socketio,
        pair="MNQ",
        trade_executor=trade_executor,
        analytics=analytics,
        point_value=2.0,
        account_balance=100000.0,
        logger=logger,
    )


@pytest.fixture
def candle_config():
    return CandleConfig()


@pytest.fixture
def strategy_numbers():
    return StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=90.0,
        extra_sl_space=0.0,
        sl_levels=[15.0, 20.0, 30.0, 40.0],
        sl_level_tolerance=3,
        min_cross_depth=5.0,
        rr_ratio=3.3,
    )


def make_strategy(
    socketio,
    line_repo,
    trade_repo,
    trade_manager,
    candle_config=None,
    options=None,
    timeframes=None,
    min_stop_loss=10.0,
    max_bounce=90.0,
    extra_sl_space=0.0,
    fixed_stop_loss=20.0,
    rr_ratio=3.3,
    sl_levels=None,
    sl_level_tolerance=5.0,
    min_cross_depth=5.0,
    trade_logger=None,
    logger=None,
    decision_log_repository=None,
    trigger_state_repo=None,
):
    return LiquidityStrategyV2(
        min_stop_loss=min_stop_loss,
        max_bounce=max_bounce,
        socketio=socketio,
        line_repository=line_repo,
        trade_repository=trade_repo,
        trade_manager=trade_manager,
        extra_sl_space=extra_sl_space,
        fixed_stop_loss=fixed_stop_loss,
        timeframes=timeframes or ["5m"],
        options=options or StrategyOptions(),
        candle_config=candle_config or CandleConfig(),
        sl_levels=sl_levels,
        sl_level_tolerance=sl_level_tolerance,
        min_cross_depth=min_cross_depth,
        rr_ratio=rr_ratio,
        trade_logger=trade_logger,
        point_value=2.0,
        account_balance=100000.0,
        logger=logger or FakeLogger(),
        decision_log_repository=decision_log_repository,
        trigger_state_repo=trigger_state_repo,
    )


@pytest.fixture
def strategy(socketio, line_repo, trade_repo, trade_manager, candle_config):
    return make_strategy(
        socketio=socketio,
        line_repo=line_repo,
        trade_repo=trade_repo,
        trade_manager=trade_manager,
        candle_config=candle_config,
    )


def make_bar(
    time=1000,
    open_=100.0,
    high=102.0,
    low=98.0,
    close=101.0,
    volume=100,
    pair="MNQ",
    tf=None,
):
    bar = {
        "time": time,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "pair": pair,
    }
    if tf:
        bar["tf"] = tf
    return bar


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session():
    """Provide a fresh in-memory SQLite database session for each test."""
    database = get_database("sqlite:///:memory:")
    database.create_tables(Base)
    session = database.get_session()
    try:
        yield session
    finally:
        session.close()
        database.get_engine().dispose()


@pytest.fixture
def setup_test_database():
    """Set up the global database with an in-memory SQLite for repository tests."""
    from src.infrastructure.database import database as db_module
    original_db = db_module.db
    test_db = get_database("sqlite:///:memory:")
    test_db.create_tables(Base)
    db_module.db = test_db
    try:
        yield test_db
    finally:
        db_module.db = original_db
        test_db.get_engine().dispose()


# ---------------------------------------------------------------------------
# Flask app fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def flask_app():
    """Create a minimal Flask app for route/controller testing."""
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(flask_app):
    """Flask test client."""
    return flask_app.test_client()
