"""E2E test: accounts only receive ORDER_OPEN commands for assigned instruments.

Extends the live-account filtering harness to cover instrument-symbol
assignments.  Accounts are configured as:

* Sim101 -> MNQ only
* Sim102 -> ES only
* Sim103 -> MNQ + ES
* Sim104 -> no instruments (strict default -> no trades)
* Sim105 -> live disabled

When an MNQ entry fires, only Sim101 and Sim103 must receive ``order_open``
commands.  When an ES entry fires, only Sim102 and Sim103 must receive them.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app_factory import AppWiring, Repositories, create_app
from src.config.models import AccountConfig
from src.domain.models import Instrument
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


def _registry_for(pair: str):
    """Single-instrument registry with the real NT full_name for *pair*."""
    class _Registry:
        def get_all(self):
            return [Instrument(symbol=pair, full_name=f"{pair} 09-26", point_value=2.0)]
    return _Registry()


def _force_ready(app: AppWiring) -> None:
    ctx = app.strategy.execution_context
    assert ctx is not None
    sm = ctx._state_machine
    sm.connect()
    sm.history_loaded()
    sm.warmup_complete()
    sm.live_bar_received()


def _make_app(pair: str, free_ports: dict[str, str], e2e_logger, account_configs: list[AccountConfig]):
    original_db = db_module.db
    test_db = _setup_in_memory_db()

    config = GatewayConfig(
        market_data_pub=free_ports["market_data"],
        command_pull=free_ports["commands"],
        query_rep=free_ports["queries"],
        heartbeat_pub=free_ports["heartbeat"],
        platform_connects=True,
    )
    gateway = TradingGateway(e2e_logger, config=config)
    data_source = ZMQDataSource(e2e_logger, gateway=gateway)

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
        pair=pair,
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
        instrument_registry=_registry_for(pair),
        session_end_time="23:59",
    )

    data_source.start()
    return wiring, original_db, test_db


@pytest.fixture
def live_app_mnq(free_ports, e2e_logger):
    account_configs = [
        AccountConfig(name="Sim101", risk_usd=500.0, rr_ratio=3.0, live_enabled=True, instrument_symbols=["MNQ"]),
        AccountConfig(name="Sim102", risk_usd=500.0, rr_ratio=3.0, live_enabled=True, instrument_symbols=["ES"]),
        AccountConfig(name="Sim103", risk_usd=500.0, rr_ratio=3.0, live_enabled=True, instrument_symbols=["MNQ", "ES"]),
        AccountConfig(name="Sim104", risk_usd=500.0, rr_ratio=3.0, live_enabled=True, instrument_symbols=[]),
        AccountConfig(name="Sim105", risk_usd=500.0, rr_ratio=3.0, live_enabled=False, instrument_symbols=["MNQ", "ES"]),
    ]
    wiring, original_db, test_db = _make_app("MNQ", free_ports, e2e_logger, account_configs)
    try:
        yield wiring
    finally:
        wiring.data_source.stop()
        _teardown_in_memory_db(original_db, test_db)


@pytest.fixture
def live_app_es(free_ports, e2e_logger):
    account_configs = [
        AccountConfig(name="Sim101", risk_usd=500.0, rr_ratio=3.0, live_enabled=True, instrument_symbols=["MNQ"]),
        AccountConfig(name="Sim102", risk_usd=500.0, rr_ratio=3.0, live_enabled=True, instrument_symbols=["ES"]),
        AccountConfig(name="Sim103", risk_usd=500.0, rr_ratio=3.0, live_enabled=True, instrument_symbols=["MNQ", "ES"]),
        AccountConfig(name="Sim104", risk_usd=500.0, rr_ratio=3.0, live_enabled=True, instrument_symbols=[]),
        AccountConfig(name="Sim105", risk_usd=500.0, rr_ratio=3.0, live_enabled=False, instrument_symbols=["MNQ", "ES"]),
    ]
    wiring, original_db, test_db = _make_app("ES", free_ports, e2e_logger, account_configs)
    try:
        yield wiring
    finally:
        wiring.data_source.stop()
        _teardown_in_memory_db(original_db, test_db)


@pytest.fixture
def fake_nt_assignment(free_ports, e2e_logger):
    accounts = ["Sim101", "Sim102", "Sim103", "Sim104", "Sim105"]
    nt = FakeNinjaTrader(addresses=free_ports, accounts=accounts, logger=e2e_logger)
    nt.start()
    try:
        yield nt
    finally:
        nt.stop()


@pytest.fixture
def e2e_harness_mnq(fake_nt_assignment, live_app_mnq):
    time.sleep(0.3)
    fake_nt_assignment.send_connect(pair="MNQ")
    time.sleep(0.1)
    _force_ready(live_app_mnq)
    yield type("Harness", (), {"nt": fake_nt_assignment, "app": live_app_mnq})()


@pytest.fixture
def e2e_harness_es(fake_nt_assignment, live_app_es):
    time.sleep(0.3)
    fake_nt_assignment.send_connect(pair="ES")
    time.sleep(0.1)
    _force_ready(live_app_es)
    yield type("Harness", (), {"nt": fake_nt_assignment, "app": live_app_es})()


class TestAccountInstrumentAssignment:
    def _build_trade(self, pair: str) -> dict:
        return {
            "pair": pair,
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

    def test_mnq_entry_only_reaches_mnq_assigned_accounts(self, e2e_harness_mnq):
        app = e2e_harness_mnq.app
        nt = e2e_harness_mnq.nt

        app.strategy._store_and_emit_open(self._build_trade("MNQ"))

        open_cmds = nt.wait_for_command_count(2, timeout=5.0, msg_type="order_open")
        assert len(open_cmds) == 2
        accounts = {c["payload"].get("account") for c in open_cmds}
        assert accounts == {"Sim101", "Sim103"}

        excluded = {"Sim102", "Sim104", "Sim105"}
        for cmd in nt.commands_received:
            if cmd.get("msg_type") == "order_open":
                assert cmd["payload"].get("account") not in excluded

    def test_es_entry_only_reaches_es_assigned_accounts(self, e2e_harness_es):
        app = e2e_harness_es.app
        nt = e2e_harness_es.nt

        app.strategy._store_and_emit_open(self._build_trade("ES"))

        open_cmds = nt.wait_for_command_count(2, timeout=5.0, msg_type="order_open")
        assert len(open_cmds) == 2
        accounts = {c["payload"].get("account") for c in open_cmds}
        assert accounts == {"Sim102", "Sim103"}

        excluded = {"Sim101", "Sim104", "Sim105"}
        for cmd in nt.commands_received:
            if cmd.get("msg_type") == "order_open":
                assert cmd["payload"].get("account") not in excluded
