"""End-to-end test: one backend streaming multiple instruments simultaneously.

Covers the multi-instrument plumbing:

1. The default pair (MNQ) streams as usual after CONNECT + history.
2. When a client joins a second instrument (MES), the ``StreamCoordinator``
   activator makes the data source send a per-instrument ``subscribe`` and
   ``refresh_request`` to the platform.
3. MES history and live bars land in the MES per-pair cache / MES session and
   never pollute the default ``_historical_bars`` cache.
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
from src.infrastructure.market_closure_filter import MarketClosureFilter
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
    """Registry with MNQ (default) and MES."""

    def get_all(self) -> list[Instrument]:
        return [
            Instrument(symbol="MNQ", full_name="MNQ 09-26", point_value=2.0),
            Instrument(symbol="MES", full_name="MES 09-26", point_value=5.0),
        ]

    def save(self, instruments: list[Instrument]) -> None:
        pass


@pytest.fixture
def live_app_two_instruments(
    free_ports: dict[str, str],
    e2e_logger: FakeLogger,
):
    """Full live wiring whose registry contains MNQ + MES."""
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
    data_source = ZMQDataSource(e2e_logger, gateway=gateway, pair="MNQ", market_filter=MarketClosureFilter(instrument="MNQ"))
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


def _make_bar(t: int, open_: float, high: float, low: float, close: float,
              volume: int = 100, pair: str = "MNQ") -> dict[str, Any]:
    return {
        "time": t,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "pair": pair,
    }


def _build_history(bar_count: int, pair: str) -> list[dict[str, Any]]:
    """Contiguous fresh 1m bars ending at the current minute boundary."""
    end = (int(time.time()) // 60) * 60
    start = end - (bar_count - 1) * 60
    return [
        _make_bar(start + i * 60, 21000.0 + i, 21010.0 + i, 20990.0 + i, 21005.0 + i, pair=pair)
        for i in range(bar_count)
    ]


def _command_seen(nt: FakeNinjaTrader, msg_type: str, instrument: str) -> bool:
    return any(
        c["msg_type"] == msg_type and c["payload"].get("instrument") == instrument
        for c in nt.commands_received
    )


def test_join_second_instrument_streams_alongside_default(
    fake_nt: FakeNinjaTrader,
    live_app_two_instruments: AppWiring,
) -> None:
    app = live_app_two_instruments
    data_source = app.data_source
    coordinator = app.coordinator
    assert coordinator is not None

    # (a) Connect the platform and let the default pair reach STREAMING.
    time.sleep(0.3)  # slow-joiner protection
    fake_nt.send_connect(pair="MNQ")
    _wait_until(
        lambda: _command_seen(fake_nt, "subscribe", "MNQ 09-26"),
        description="default MNQ subscribe command",
    )
    fake_nt.send_history_batch(_build_history(60, "MNQ"))
    fake_nt.send_history_end()
    _wait_until(lambda: data_source.is_streaming, description="default pair STREAMING")

    # (b) Simulate a browser joining the MES instrument room.
    coordinator.join_instrument("MES", "fake-sid")

    # The activator must subscribe MES and request its history.
    _wait_until(
        lambda: _command_seen(fake_nt, "subscribe", "MES 09-26"),
        description="MES subscribe command",
    )
    _wait_until(
        lambda: _command_seen(fake_nt, "refresh_request", "MES 09-26"),
        description="MES refresh_request command",
    )

    # (c) MES history arrives tagged with pair=MES; it must land in the
    # per-pair cache only, and the default lifecycle state must be untouched.
    mes_history = _build_history(30, "MES")
    fake_nt.send_history_batch(mes_history, pair="MES")
    fake_nt.send_history_end(pair="MES")
    _wait_until(
        lambda: len(data_source.load_historical_bars("1m", pair="MES")) == 30,
        description="MES history in per-pair cache",
    )
    assert all(b["pair"] == "MNQ" for b in data_source._historical_bars)
    assert data_source.is_streaming

    # The MES session exists, was started, and received the history batch.
    mes_session = coordinator.get_session("MES")
    assert mes_session is not None
    assert mes_session._started

    # (d) A live MES bar lands in the MES cache only.
    live_bar = _make_bar(
        mes_history[-1]["time"] + 60, 6000.0, 6005.0, 5995.0, 6002.0, 500, pair="MES",
    )
    fake_nt.send_bar(live_bar)
    _wait_until(
        lambda: any(
            b["time"] == live_bar["time"]
            for b in data_source.load_historical_bars("1m", pair="MES")
        ),
        description="live MES bar in per-pair cache",
    )
    assert all(b["pair"] == "MNQ" for b in data_source._historical_bars)

    # (e) The default MNQ stream is unaffected and keeps working.
    mnq_bar = _make_bar(
        data_source._historical_bars[-1]["time"] + 60,
        22000.0, 22010.0, 21990.0, 22005.0, 400, pair="MNQ",
    )
    fake_nt.send_bar(mnq_bar)
    _wait_until(
        lambda: any(b["time"] == mnq_bar["time"] for b in data_source._historical_bars),
        description="live MNQ bar in default cache",
    )
