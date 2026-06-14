"""Tests for TSI logging/event behaviour during warm-up."""

import pytest

from src.application.live_readiness.trading_context import AlwaysEnabledTradingContext
from src.domain.readiness.protocols import IExecutionContext
from src.strategies.liquidity_v2.config import CandleConfig
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
from tests.conftest import make_bar, make_strategy
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLineRepository,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
    MutableTradingContext,
)
from src.services.trade_manager import TradeManager


class RecordingLogger(FakeLogger):
    def __init__(self):
        self.messages = []

    def info(self, message: str) -> None:
        self.messages.append(message)


def _make_warmup_strategy(warmup=True, trading_enabled=False):
    sio = DummySocketIO()
    lr = FakeLineRepository()
    tr = FakeTradeRepository()
    logger = RecordingLogger()
    tm = TradeManager(
        tr,
        sio,
        trade_executor=FakeTradeExecutor(),
        analytics=FakeAnalyticsReporter(),
        point_value=2.0,
        account_balance=100000.0,
        logger=logger,
    )
    ctx = MutableTradingContext(trading_enabled=trading_enabled, warmup=warmup)
    strat = make_strategy(
        sio,
        lr,
        tr,
        tm,
        logger=logger,
        execution_context=ctx,
    )
    return strat, sio, logger


class TestTSIWarmupLogging:
    def test_no_tsi_info_logs_during_warmup(self):
        strat, _, logger = _make_warmup_strategy(warmup=True)

        # Feed enough raw bars to produce several TSI calculations.
        base_time = 1_781_000_000
        for i in range(50):
            strat.on_raw_bar(make_bar(time=base_time + i * 60, close=100.0 + i * 0.1))

        tsi_logs = [m for m in logger.messages if "[TSI:" in m]
        assert len(tsi_logs) == 0, f"Expected no TSI logs during warmup, got {len(tsi_logs)}"

    def test_no_indicator_update_events_during_warmup(self):
        strat, sio, _ = _make_warmup_strategy(warmup=True)

        base_time = 1_781_000_000
        for i in range(50):
            strat.on_raw_bar(make_bar(time=base_time + i * 60, close=100.0 + i * 0.1))

        indicator_events = [e for e in sio.events if e[0] == "indicator_update"]
        assert len(indicator_events) == 0

    def test_tsi_logging_resumes_after_warmup(self):
        strat, _, logger = _make_warmup_strategy(warmup=True)
        base_time = 1_781_000_000
        for i in range(50):
            strat.on_raw_bar(make_bar(time=base_time + i * 60, close=100.0 + i * 0.1))

        # End warm-up and process one more bar.
        strat.execution_context.warmup = False
        strat.on_raw_bar(make_bar(time=base_time + 50 * 60, close=105.0))

        tsi_logs = [m for m in logger.messages if "[TSI:" in m]
        assert len(tsi_logs) > 0

    def test_warmup_summary_is_logged(self):
        strat, _, logger = _make_warmup_strategy(warmup=True)
        base_time = 1_781_000_000
        for i in range(50):
            strat.on_raw_bar(make_bar(time=base_time + i * 60, close=100.0 + i * 0.1))

        strat.execution_context.warmup = False
        strat.on_raw_bar(make_bar(time=base_time + 50 * 60, close=105.0))

        summaries = [m for m in logger.messages if "[TSI] Warm-up complete" in m]
        assert len(summaries) == 1
