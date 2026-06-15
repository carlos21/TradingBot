"""pytest fixtures for ZeroMQ end-to-end tests.

Wires a FakeNinjaTrader to the real TradingGateway / app_factory so that
every test exercises the exact same code path as live trading.
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import pytest

from app_factory import AppWiring, Repositories, create_app
from src.config.models import AccountConfig
from src.infrastructure.database import database as db_module
from src.infrastructure.database.database_protocol import Base, get_database
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.gateway.executor import MultiAccountExecutor, ZMQTradeExecutor
from src.infrastructure.gateway.gateway import GatewayConfig, TradingGateway
from src.strategies.liquidity_v2.base_strategy import LineRemovalMode
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from src.strategies.liquidity_v2.prod_config import (
    get_prod_candle_config,
    get_prod_strategy_numbers,
    get_prod_strategy_options,
)
from tests.fake_ninjatrader.fake_nt import FakeNinjaTrader
from tests.fake_ninjatrader.port_helper import get_free_ports
from tests.fakes import FakeLineRepository, FakeLogger, FakeTradeRepository

# Prevent Flask from needing a real secret key
os.environ.setdefault("SECRET_KEY", "test-secret")


@dataclass
class E2EHarness:
    """Holder that gives tests easy access to both sides of the wire."""
    nt: FakeNinjaTrader
    app: AppWiring


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _default_numbers() -> StrategyNumbers:
    return StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=90.0,
        extra_sl_space=0.0,
        fixed_stop_loss=20.0,
        rr_ratio=3.3,
        point_value=2.0,
        account_balance=100000.0,
    )


def _setup_in_memory_db() -> Any:
    """Replace the global DB with an in-memory SQLite instance."""
    test_db = get_database("sqlite:///:memory:")
    test_db.create_tables(Base)
    db_module.db = test_db
    return test_db


def _teardown_in_memory_db(original_db: Any, test_db: Any) -> None:
    db_module.db = original_db
    test_db.get_engine().dispose()


# ---------------------------------------------------------------------------
# Port fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def free_ports() -> dict[str, str]:
    ports = get_free_ports(4)
    return {
        "market_data": f"tcp://127.0.0.1:{ports[0]}",
        "commands": f"tcp://127.0.0.1:{ports[1]}",
        "queries": f"tcp://127.0.0.1:{ports[2]}",
        "heartbeat": f"tcp://127.0.0.1:{ports[3]}",
    }


# ---------------------------------------------------------------------------
# Logger fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e_logger() -> FakeLogger:
    return FakeLogger()


# ---------------------------------------------------------------------------
# Fake NinjaTrader fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_nt(
    free_ports: dict[str, str],
    e2e_logger: FakeLogger,
) -> Generator[FakeNinjaTrader, None, None]:
    """Started FakeNinjaTrader connected to ephemeral ports."""
    nt = FakeNinjaTrader(
        addresses=free_ports,
        accounts=["Sim101"],
        logger=e2e_logger,
    )
    nt.start()
    try:
        yield nt
    finally:
        nt.stop()


@pytest.fixture
def fake_nt_auto(
    free_ports: dict[str, str],
    e2e_logger: FakeLogger,
) -> Generator[FakeNinjaTrader, None, None]:
    """FakeNinjaTrader with auto-fill enabled for CSV scenario tests."""
    nt = FakeNinjaTrader(
        addresses=free_ports,
        accounts=["Sim101"],
        logger=e2e_logger,
        auto_fill_entries=True,
        auto_fill_exits=True,
    )
    nt.start()
    try:
        yield nt
    finally:
        nt.stop()


# ---------------------------------------------------------------------------
# Live app fixtures (single-account)
# ---------------------------------------------------------------------------


@pytest.fixture
def live_app(
    free_ports: dict[str, str],
    e2e_logger: FakeLogger,
) -> Generator[AppWiring, None, None]:
    """Full TradingBot wiring in live_mode with real ZMQ sockets."""
    original_db = db_module.db
    test_db = _setup_in_memory_db()

    # Build ZMQ components pointing at the fake NT
    config = GatewayConfig(
        market_data_pub=free_ports["market_data"],
        command_pull=free_ports["commands"],
        query_rep=free_ports["queries"],
        heartbeat_pub=free_ports["heartbeat"],
        platform_connects=True,
    )
    gateway = TradingGateway(e2e_logger, config=config, pair="MNQ", instrument="MNQ 09-26")
    data_source = ZMQDataSource(e2e_logger, gateway=gateway, pair="MNQ")
    trade_executor = ZMQTradeExecutor(gateway, e2e_logger, risk_usd=500)

    repos = Repositories(
        lines=FakeLineRepository(),
        trades=FakeTradeRepository(),
    )

    wiring = create_app(
        pair="MNQ",
        data_source=data_source,
        repos=repos,
        numbers=_default_numbers(),
        options=DEFAULT_STRATEGY_OPTIONS,
        candle_config=CandleConfig(),
        timeframes=["5m"],
        live_mode=True,
        trade_executor=trade_executor,
        logger=e2e_logger,
        db=test_db,
        session_end_time="23:59",  # late so tests control session-end explicitly
    )

    data_source.start()
    try:
        yield wiring
    finally:
        data_source.stop()
        _teardown_in_memory_db(original_db, test_db)


@pytest.fixture
def live_app_scenario(
    free_ports: dict[str, str],
    e2e_logger: FakeLogger,
) -> Generator[AppWiring, None, None]:
    """Live app wired with prod strategy config (same as scenario runner)."""
    original_db = db_module.db
    test_db = _setup_in_memory_db()

    config = GatewayConfig(
        market_data_pub=free_ports["market_data"],
        command_pull=free_ports["commands"],
        query_rep=free_ports["queries"],
        heartbeat_pub=free_ports["heartbeat"],
        platform_connects=True,
    )
    gateway = TradingGateway(e2e_logger, config=config, pair="MNQ", instrument="MNQ 09-26")
    data_source = ZMQDataSource(e2e_logger, gateway=gateway, pair="MNQ")
    trade_executor = ZMQTradeExecutor(gateway, e2e_logger, risk_usd=500)

    repos = Repositories(
        lines=FakeLineRepository(),
        trades=FakeTradeRepository(),
    )

    numbers = get_prod_strategy_numbers(rr_ratio=5.0)
    options = get_prod_strategy_options(
        max_bounce=numbers.max_bounce,
        min_cross_depth=numbers.min_cross_depth,
        skip_rollover_days=False,
        reentry_only=False,
        line_removal_mode=LineRemovalMode.ON_EVALUATE,
        max_reentry_attempts=DEFAULT_STRATEGY_OPTIONS.max_reentry_attempts,
    )

    wiring = create_app(
        pair="MNQ",
        data_source=data_source,
        repos=repos,
        numbers=numbers,
        options=options,
        candle_config=get_prod_candle_config(),
        timeframes=["3m", "5m", "15m", "30m", "1h"],
        live_mode=True,
        trade_executor=trade_executor,
        logger=e2e_logger,
        db=test_db,
        session_end_time="23:59",
    )

    data_source.start()
    try:
        yield wiring
    finally:
        data_source.stop()
        _teardown_in_memory_db(original_db, test_db)


# ---------------------------------------------------------------------------
# Live app fixture (multi-account)
# ---------------------------------------------------------------------------


@pytest.fixture
def live_app_multi(
    free_ports: dict[str, str],
    e2e_logger: FakeLogger,
) -> Generator[AppWiring, None, None]:
    """Full TradingBot wiring in live_mode with MultiAccountExecutor."""
    original_db = db_module.db
    test_db = _setup_in_memory_db()

    account_configs = [
        AccountConfig(name="Sim101", risk_usd=500.0, rr_ratio=3.0),
        AccountConfig(name="Sim102", risk_usd=1000.0, rr_ratio=5.0),
    ]

    config = GatewayConfig(
        market_data_pub=free_ports["market_data"],
        command_pull=free_ports["commands"],
        query_rep=free_ports["queries"],
        heartbeat_pub=free_ports["heartbeat"],
        platform_connects=True,
    )
    gateway = TradingGateway(e2e_logger, config=config, pair="MNQ", instrument="MNQ 09-26")
    data_source = ZMQDataSource(e2e_logger, gateway=gateway, pair="MNQ")

    # Share the same gateway between data_source and trade executor
    zmq_executor = ZMQTradeExecutor(gateway, e2e_logger, risk_usd=500)
    multi_executor = MultiAccountExecutor(
        trade_manager=None,  # injected by create_app
        account_configs=account_configs,
        gateway_executor=zmq_executor,
        logger=e2e_logger,
    )

    repos = Repositories(
        lines=FakeLineRepository(),
        trades=FakeTradeRepository(),
    )

    wiring = create_app(
        pair="MNQ",
        data_source=data_source,
        repos=repos,
        numbers=_default_numbers(),
        options=DEFAULT_STRATEGY_OPTIONS,
        candle_config=CandleConfig(),
        timeframes=["5m"],
        live_mode=True,
        trade_executor=multi_executor,
        logger=e2e_logger,
        db=test_db,
        session_end_time="23:59",
    )

    data_source.start()
    try:
        yield wiring
    finally:
        data_source.stop()
        _teardown_in_memory_db(original_db, test_db)


# ---------------------------------------------------------------------------
# Combined harness fixtures
# ---------------------------------------------------------------------------


def _force_ready(app: AppWiring) -> None:
    """Force the live readiness state machine to LIVE for tests.

    E2E tests wire the real readiness monitor; without enough historical bars
    the machine would stay in WARMING_UP and the strategy would not trade.
    Tests that exercise order/command wiring can shortcut the warm-up phase.
    """
    ctx = getattr(app.strategy, "execution_context", None)
    sm = getattr(ctx, "_state_machine", None)
    if sm is None:
        return
    sm.connect()
    sm.history_loaded()
    sm.warmup_complete()
    sm.live_bar_received()


@pytest.fixture
def e2e_harness(
    fake_nt: FakeNinjaTrader,
    live_app: AppWiring,
) -> Generator[E2EHarness, None, None]:
    """Both sides started and CONNECT handshake performed."""
    # Allow ZMQ connections to establish (slow-joiner protection)
    time.sleep(0.3)
    fake_nt.send_connect(pair="MNQ")
    time.sleep(0.1)
    _force_ready(live_app)
    yield E2EHarness(nt=fake_nt, app=live_app)


@pytest.fixture
def e2e_harness_multi(
    fake_nt: FakeNinjaTrader,
    live_app_multi: AppWiring,
) -> Generator[E2EHarness, None, None]:
    """Multi-account variant of the harness."""
    # Update fake NT accounts to match the multi-account config
    fake_nt._accounts = ["Sim101", "Sim102"]
    fake_nt._tracker = FakeNinjaTrader.__new__(FakeNinjaTrader)
    # Re-init tracker with multi accounts
    from tests.fake_ninjatrader.order_tracker import FakeOrderTracker
    fake_nt._tracker = FakeOrderTracker(["Sim101", "Sim102"])
    time.sleep(0.3)
    fake_nt.send_connect(pair="MNQ")
    time.sleep(0.1)
    _force_ready(live_app_multi)
    yield E2EHarness(nt=fake_nt, app=live_app_multi)


@pytest.fixture
def e2e_harness_auto(
    fake_nt_auto: FakeNinjaTrader,
    live_app_scenario: AppWiring,
) -> Generator[E2EHarness, None, None]:
    """Harness with auto-fill FakeNT for CSV-driven scenario tests."""
    time.sleep(0.3)
    fake_nt_auto.send_connect(pair="MNQ")
    time.sleep(0.1)
    _force_ready(live_app_scenario)
    yield E2EHarness(nt=fake_nt_auto, app=live_app_scenario)
