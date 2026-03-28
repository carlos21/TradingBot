import pytest
from tests.fakes import (
    DummySocketIO,
    FakeLineRepository,
    FakeTradeRepository,
    FakeTradeExecutor,
)
from src.services.trade_manager import TradeManager
from src.services.trade_logger import TradeLogger
from src.strategies.strategy_config import CandleConfig, StrategyNumbers
from src.strategies.base_liquidity_strategy import StrategyOptions, BreakevenConfig, LineRemovalMode
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2


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
def trade_manager(trade_repo, socketio, trade_executor):
    return TradeManager(
        trade_repository=trade_repo,
        socketio=socketio,
        pair="NQ",
        trade_executor=trade_executor,
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
    pair="NQ",
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
