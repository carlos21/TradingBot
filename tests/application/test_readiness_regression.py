"""Regression test for the 2026-06-11 missed-entry bug.

The bug happened because the old ZMQDataSource maintained a `_warmup_done` flag.
When NinjaTrader returned an empty history on the first request and then sent a
REFRESH_START -> history_batch -> HISTORY_END cycle, the second history load was
ignored and the strategy's indicators never warmed up.  The 07:35 entry was
missed as a result.

This integration test drives the real ReadinessMonitor/WarmupOrchestrator with
no ZMQ sockets, so it is deterministic and fast, while still exercising the
exact readiness state transitions.
"""

from __future__ import annotations

import time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from dateutil import parser as dtparser

from src.application.live_readiness.live_bar_buffer import LiveBarBuffer
from src.application.live_readiness.readiness_monitor import ReadinessMonitor
from src.application.live_readiness.trading_context import ReadinessTradingContext
from src.application.live_readiness.warmup_orchestrator import WarmupOrchestrator
from src.application.live_readiness.warmup_policy import MinimumBarsWarmupPolicy
from src.domain.readiness import ReadinessStateMachine
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_v2.base_strategy import LineRemovalMode
from src.strategies.liquidity_v2.prod_config import (
    get_prod_candle_config,
    get_prod_strategy_numbers,
    get_prod_strategy_options,
)
from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLineRepository,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)

CSV_FILE = "csvs/NQ_live.csv"

# Embedded test scenario so tests are not coupled to test_scenario.yaml,
# which can change over time.
_TEST_SCENARIO: dict[str, Any] = {
    "name": "MNQ - 2026-06-11",
    "pair": "MNQ",
    "tf": "1m",
    "start": "2026-06-11 06:00:00Z",
    "end": "2026-06-11 16:00:00Z",
    "lines": [
        {"price": 28688.00, "at": "2026-06-11 07:15:00"},
    ],
    "expect": {
        "entry": 28696.75,
        "sl": 28656.75,
        "tp": 28896.75,
        "reentry": {
            "entry": 28703.00,
            "sl": 28663.00,
            "tp": 28903.00,
        },
    },
    "show_tsi": False,
}


def _to_epoch(dt_str: str, tz_name: str = "America/Chicago") -> int:
    dt = dtparser.parse(dt_str)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    tz = ZoneInfo(tz_name)
    dt = dt.replace(tzinfo=tz)
    return int(dt.astimezone(ZoneInfo("UTC")).timestamp())


def _load_csv_bars() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]
]:
    from tests.fake_ninjatrader.csv_bar_loader import load_bars

    scenario = _TEST_SCENARIO
    start_ts = _to_epoch(scenario["start"])
    end_ts = start_ts + 3 * 3600
    warmup_start_ts = start_ts - 8 * 3600

    raw_bars = load_bars(
        CSV_FILE,
        pair=scenario["pair"],
        start_time=warmup_start_ts,
        end_time=end_ts,
    )
    warmup_bars_raw = [b for b in raw_bars if b["time"] < start_ts]
    live_bars_raw = [b for b in raw_bars if b["time"] >= start_ts]
    return warmup_bars_raw, live_bars_raw, scenario


def _make_strategy(state_machine: ReadinessStateMachine) -> LiquidityStrategyV2:
    logger = FakeLogger()
    event_publisher = DummySocketIO()
    trade_repo = FakeTradeRepository()
    trade_manager = TradeManager(
        trade_repo,
        event_publisher,
        trade_executor=FakeTradeExecutor(),
        analytics=FakeAnalyticsReporter(),
        point_value=2.0,
        account_balance=100000.0,
        logger=logger,
    )
    numbers = get_prod_strategy_numbers(rr_ratio=5.0)
    options = get_prod_strategy_options(
        max_bounce=numbers.max_bounce,
        min_cross_depth=numbers.min_cross_depth,
        skip_rollover_days=False,
        reentry_only=False,
        line_removal_mode=LineRemovalMode.ON_EVALUATE,
        max_reentry_attempts=3,
    )
    # Disable the trading-hours filter so the historical 07:35 entry can fire.
    for i, f in enumerate(options.entry_filters):
        if getattr(f, "__name__", "") == "time_range":
            options.entry_filters[i] = lambda _ctx: (True, "ok")
            break

    return LiquidityStrategyV2(
        min_stop_loss=numbers.min_stop_loss,
        max_bounce=numbers.max_bounce,
        event_publisher=event_publisher,
        line_repository=FakeLineRepository(),
        trade_repository=trade_repo,
        trade_manager=trade_manager,
        extra_sl_space=0.0,
        fixed_stop_loss=numbers.fixed_stop_loss,
        options=options,
        candle_config=get_prod_candle_config(),
        timeframes=["3m", "5m", "15m", "30m", "1h"],
        point_value=numbers.point_value,
        account_balance=numbers.account_balance,
        sl_levels=numbers.sl_levels,
        max_entry_distance=getattr(numbers, "max_entry_distance", 50.0),
        sl_level_tolerance=getattr(numbers, "sl_level_tolerance", 3.0),
        logger=logger,
        execution_context=ReadinessTradingContext(state_machine),
    )


class _FakeDataSource:
    """Minimal data source that records refresh requests and bypasses completeness."""

    def __init__(self) -> None:
        self.requests: list[int | None] = []

    def request_refresh(self, days: int | None = None) -> None:
        self.requests.append(days)

    def check_history_completeness(
        self, bars: list[dict[str, Any]] | None = None
    ) -> tuple[bool, str]:
        return True, "test"


class TestReadinessRegression:
    """Empty history must block live bars until a real history load completes."""

    def test_empty_history_then_refresh_fires_entry(self) -> None:
        warmup_bars_raw, live_bars_raw, scenario = _load_csv_bars()
        assert len(warmup_bars_raw) > 0, "No warmup bars"
        assert len(live_bars_raw) > 0, "No live bars"

        state_machine = ReadinessStateMachine()
        strategy = _make_strategy(state_machine)

        processed_bars: list[dict[str, Any]] = []

        def _process_bar(bar: dict[str, Any]) -> None:
            processed_bars.append(bar)
            strategy.on_raw_bar(bar)
            if strategy.options.breakeven or strategy.options.reentry_breakeven:
                strategy.check_breakeven(bar)

        fake_ds = _FakeDataSource()
        monitor = ReadinessMonitor(
            state_machine=state_machine,
            warmup_orchestrator=WarmupOrchestrator(strategy, logger=strategy.logger),
            warmup_policy=MinimumBarsWarmupPolicy(min_bars=30),
            bar_buffer=LiveBarBuffer(processor=_process_bar),
            live_bar_processor=_process_bar,
            data_source=fake_ds,
            logger=strategy.logger,
            retry_base_delay_sec=0.05,
            retry_max_delay_sec=0.05,
        )
        monitor.set_pair("MNQ")

        # 1) Cold platform connect.
        state_machine.connect()
        assert state_machine.state.name == "CONNECTED"

        # 2) Empty first history load (NinjaTrader cache is cold).
        monitor.on_history_complete([])
        assert state_machine.state.name == "WAITING_FOR_HISTORY"

        # 3) Live bars must be ignored while history is missing.
        first_live = live_bars_raw[0]
        monitor.on_live_bar(first_live)
        assert first_live not in processed_bars

        # 4) Real history arrives after a platform refresh.
        monitor.on_refresh_start()
        assert state_machine.state.name == "REFRESHING"

        monitor.on_history_complete(warmup_bars_raw)
        assert state_machine.state.name in ("READY", "WARMING_UP")

        # Higher timeframes (30m/1h) need more than 8h of bars. Force LIVE so the
        # regression focuses on the empty-history-then-refresh path rather than
        # prod warm-up durations.
        state_machine.warmup_complete()
        state_machine.live_bar_received()
        assert state_machine.state.name == "LIVE"

        # 5) Add the line AFTER the refresh warm-up replay.
        for i, line_spec in enumerate(scenario.get("lines", [])):
            price = float(line_spec["price"])
            at_str = line_spec.get("at", scenario["start"])
            creation_ts = _to_epoch(at_str)
            strategy.add_strategy_line(
                id=f"reg_line_{i}",
                level=price,
                creation_timestamp=creation_ts,
            )

        # 6) Stream live bars through the readiness monitor.
        for bar in live_bars_raw:
            monitor.on_live_bar(bar)

        # 7) Assert the expected 07:35-style entry fired.
        assert len(strategy.open_trades) >= 1, "Expected at least one entry signal"
        expected = scenario["expect"]
        matching = [
            t for t in strategy.open_trades
            if abs(t["entry"] - expected["entry"]) < 0.01
        ]
        assert matching, (
            f"No trade with expected entry {expected['entry']} "
            f"in {strategy.open_trades}"
        )
        trade = matching[0]
        assert trade["stop_loss"] == pytest.approx(expected["sl"], abs=0.01)
        assert trade["take_profit"] == pytest.approx(expected["tp"], abs=0.01)

    def test_empty_history_schedules_retry(self) -> None:
        state_machine = ReadinessStateMachine()
        strategy = _make_strategy(state_machine)

        processed: list[dict[str, Any]] = []
        fake_ds = _FakeDataSource()
        monitor = ReadinessMonitor(
            state_machine=state_machine,
            warmup_orchestrator=WarmupOrchestrator(strategy, logger=strategy.logger),
            warmup_policy=MinimumBarsWarmupPolicy(min_bars=30),
            bar_buffer=LiveBarBuffer(processor=processed.append),
            live_bar_processor=processed.append,
            data_source=fake_ds,
            logger=strategy.logger,
            retry_base_delay_sec=0.01,
            retry_max_delay_sec=0.01,
        )

        state_machine.connect()
        monitor.on_history_complete([])
        assert state_machine.state.name == "WAITING_FOR_HISTORY"
        assert state_machine.retry_count == 1

        # Wait for the retry timer to fire.
        time.sleep(0.1)
        assert len(fake_ds.requests) == 1
        assert state_machine.retry_count == 1
        assert "attempt 1" in state_machine.reason.lower()

        # A second empty history should increment the retry counter.
        monitor.on_history_complete([])
        assert state_machine.retry_count == 2
        time.sleep(0.1)
        assert len(fake_ds.requests) == 2

        monitor.stop()
