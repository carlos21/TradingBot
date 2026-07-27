"""End-to-end test: chart loads even when no live NT account is assigned.

A missing live account assignment must block *trading*, not *charting*.
This test demonstrates that the instrument still subscribes, receives
historical bars, and emits history_loaded when no account covers it.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import pytest

from app_factory import AppWiring, Repositories, create_app
from src.domain.models import Instrument
from src.infrastructure.database import database as db_module
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.gateway.executor import ZMQTradeExecutor
from src.infrastructure.gateway.gateway import GatewayConfig, TradingGateway
from src.infrastructure.repositories.accounts_repository import NtAccountRepository
from src.strategies.liquidity_v2.config import CandleConfig
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from tests.e2e.conftest import (
    _default_numbers,
    _setup_in_memory_db,
    _teardown_in_memory_db,
)
from tests.fake_ninjatrader.fake_nt import FakeNinjaTrader
from tests.fakes import FakeLineRepository, FakeLogger, FakeTradeRepository


class _TwoInstrumentRegistry:
    def get_all(self) -> list[Instrument]:
        return [
            Instrument(symbol="MNQ", full_name="MNQ 09-26", point_value=2.0),
            Instrument(symbol="MES", full_name="MES 09-26", point_value=5.0),
        ]

    def save(self, instruments: list[Instrument]) -> None:
        pass


@pytest.fixture
def live_app_mes_unassigned(
    free_ports: dict[str, str],
    e2e_logger: FakeLogger,
):
    """Live wiring where Sim101 is live-enabled only for MNQ, not MES."""
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
    trade_executor = ZMQTradeExecutor(gateway, e2e_logger, risk_usd=500)

    repos = Repositories(
        lines=FakeLineRepository(),
        trades=FakeTradeRepository(),
    )

    # Only MNQ is assigned; MES has no live account.
    accounts_repo = NtAccountRepository(db=test_db)
    accounts_repo.upsert(
        name="Sim101",
        live_enabled=True,
        instrument_symbols=["MNQ"],
    )

    wiring = create_app(
        pair="MNQ",
        data_source=data_source,
        repos=repos,
        numbers=_default_numbers(),
        options=replace(DEFAULT_STRATEGY_OPTIONS),
        candle_config=CandleConfig(),
        timeframes=["5m"],
        live_mode=True,
        trade_executor=trade_executor,
        logger=e2e_logger,
        db=test_db,
        session_end_time="23:59",
        instrument_registry=_TwoInstrumentRegistry(),
    )

    data_source.start()
    try:
        yield wiring
    finally:
        data_source.stop()
        _teardown_in_memory_db(original_db, test_db)


def _wait_until(predicate, timeout: float = 5.0, description: str = "condition") -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"Timed out ({timeout}s) waiting for {description}")


def _make_bar(t: int, pair: str) -> dict[str, Any]:
    return {
        "time": t,
        "open": 5000.0,
        "high": 5010.0,
        "low": 4990.0,
        "close": 5005.0,
        "volume": 100,
        "pair": pair,
    }


def _build_history(bar_count: int, pair: str) -> list[dict[str, Any]]:
    end = (int(time.time()) // 60) * 60
    start = end - (bar_count - 1) * 60
    return [_make_bar(start + i * 60, pair) for i in range(bar_count)]


def test_mes_chart_loads_without_assigned_nt_account(
    fake_nt: FakeNinjaTrader,
    live_app_mes_unassigned: AppWiring,
) -> None:
    """MES must chart even though no live NT account covers it."""
    app = live_app_mes_unassigned
    data_source = app.data_source
    coordinator = app.coordinator

    emitted: list[tuple[str, Any]] = []
    orig_emit = app.socketio.emit

    def _emit_catcher(event, *args, **kwargs):
        emitted.append((event, args[0] if args else kwargs.get("data")))
        return orig_emit(event, *args, **kwargs)

    app.socketio.emit = _emit_catcher

    try:
        coordinator.join_instrument("MES", "fake-sid-mes")
        time.sleep(0.3)
        fake_nt.send_connect(pair="MES")

        # The platform must still be asked to subscribe MES for charting.
        _wait_until(
            lambda: any(
                c["msg_type"] == "subscribe" and c["payload"].get("instrument") == "MES 09-26"
                for c in fake_nt.commands_received
            ),
            description="MES subscribe command despite no assigned account",
        )

        mes_history = _build_history(60, "MES")
        fake_nt.send_history_batch(mes_history, pair="MES")
        fake_nt.send_history_end(pair="MES")

        # Chart data must land in the per-pair cache.
        _wait_until(
            lambda: len(data_source.load_historical_bars("1m", pair="MES")) == 60,
            description="MES bars cached for charting",
        )

        # history_loaded must be emitted so the frontend can render the chart.
        _wait_until(
            lambda: any(e == "history_loaded" and p.get("pair") == "MES" for e, p in emitted),
            description="history_loaded emitted for MES",
        )

        # The readiness pipeline must advance past CONNECTED so the Stream
        # Health panel is not stuck on the Connected step.
        mes_session = coordinator.get_session("MES")
        assert mes_session is not None
        _wait_until(
            lambda: mes_session.readiness_monitor._state_machine.state.name
            in ("WARMING_UP", "READY", "LIVE"),
            description="MES readiness to advance past CONNECTED",
        )
    finally:
        app.socketio.emit = orig_emit
