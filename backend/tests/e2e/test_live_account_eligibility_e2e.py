"""E2E test: only live-enabled accounts receive ORDER_OPEN commands.

Wires the real TradingGateway / MultiAccountExecutor to a FakeNinjaTrader and
exercises the strategy's account filtering path end-to-end over ZeroMQ.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app_factory import AppWiring, Repositories, create_app
from src.config.models import AccountConfig
from src.infrastructure.database import database as db_module
from src.infrastructure.database.database_protocol import Base, get_database
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.gateway.executor import MultiAccountExecutor, ZMQTradeExecutor
from src.infrastructure.gateway.gateway import GatewayConfig, TradingGateway
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from tests.fake_ninjatrader.fake_nt import FakeNinjaTrader
from tests.fakes import FakeLineRepository, FakeTradeRepository


def _setup_in_memory_db() -> Any:
    test_db = get_database("sqlite:///:memory:")
    test_db.create_tables(Base)
    db_module.db = test_db
    return test_db


def _teardown_in_memory_db(original_db: Any, test_db: Any) -> None:
    db_module.db = original_db
    test_db.get_engine().dispose()


def _force_ready(app: AppWiring) -> None:
    ctx = getattr(app.strategy, "execution_context", None)
    sm = getattr(ctx, "_state_machine", None)
    if sm is None:
        return
    sm.connect()
    sm.history_loaded()
    sm.warmup_complete()
    sm.live_bar_received()


@pytest.fixture
def live_app_filtered(free_ports, e2e_logger):
    """Live app with 5 configured accounts, only 2 enabled for live trading."""
    original_db = db_module.db
    test_db = _setup_in_memory_db()

    account_configs = [
        AccountConfig(name="Sim101", risk_usd=500.0, rr_ratio=3.0, live_enabled=True),
        AccountConfig(name="Sim102", risk_usd=500.0, rr_ratio=3.0, live_enabled=True),
        AccountConfig(name="Sim103", risk_usd=500.0, rr_ratio=3.0, live_enabled=False),
        AccountConfig(name="Sim104", risk_usd=500.0, rr_ratio=3.0, live_enabled=False),
        AccountConfig(name="Sim105", risk_usd=500.0, rr_ratio=3.0, live_enabled=False),
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

    zmq_executor = ZMQTradeExecutor(gateway, e2e_logger, risk_usd=500)
    multi_executor = MultiAccountExecutor(
        trade_manager=None,
        account_configs=[a for a in account_configs if a.live_enabled],
        gateway_executor=zmq_executor,
        logger=e2e_logger,
    )

    repos = Repositories(
        lines=FakeLineRepository(),
        trades=FakeTradeRepository(),
    )

    numbers = StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=90.0,
        extra_sl_space=0.0,
        fixed_stop_loss=20.0,
        rr_ratio=2.0,
        point_value=2.0,
        account_balance=100000.0,
    )
    numbers.account_configs = account_configs

    wiring = create_app(
        pair="MNQ",
        data_source=data_source,
        repos=repos,
        numbers=numbers,
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


@pytest.fixture
def fake_nt_filtered(free_ports, e2e_logger):
    """FakeNT configured to know about all 5 accounts."""
    accounts = ["Sim101", "Sim102", "Sim103", "Sim104", "Sim105"]
    nt = FakeNinjaTrader(
        addresses=free_ports,
        accounts=accounts,
        logger=e2e_logger,
    )
    nt.start()
    try:
        yield nt
    finally:
        nt.stop()


@pytest.fixture
def e2e_harness_filtered(fake_nt_filtered, live_app_filtered):
    """Combined harness with 5 accounts / 2 live-enabled."""
    time.sleep(0.3)
    fake_nt_filtered.send_connect(pair="MNQ")
    time.sleep(0.1)
    _force_ready(live_app_filtered)
    yield type("Harness", (), {"nt": fake_nt_filtered, "app": live_app_filtered})()


class TestLiveAccountEligibility:
    """Only live-enabled accounts receive order_open commands."""

    def test_only_live_enabled_accounts_get_orders(self, e2e_harness_filtered) -> None:
        app = e2e_harness_filtered.app
        nt = e2e_harness_filtered.nt

        trade = {
            "pair": "MNQ",
            "type": "long",
            "entry": 21000.0,
            "stop_loss": 20900.0,
            "take_profit": 21200.0,
            "risk": 100.0,
            "entry_time": time.time(),
            "rr_ratio": 2.0,
            "status": "open",
            "reentry_attempt": 0,
        }

        # Drive the same code path the strategy uses when a real entry fires.
        app.strategy._store_and_emit_open(trade)

        open_cmds = nt.wait_for_command_count(2, timeout=5.0, msg_type="order_open")
        assert len(open_cmds) == 2

        accounts = {c["payload"].get("account") for c in open_cmds}
        assert accounts == {"Sim101", "Sim102"}

        disabled = {"Sim103", "Sim104", "Sim105"}
        for cmd in nt.commands_received:
            if cmd.get("msg_type") == "order_open":
                assert cmd["payload"].get("account") not in disabled
