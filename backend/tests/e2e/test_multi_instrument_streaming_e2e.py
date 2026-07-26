"""End-to-end test: one backend streaming multiple instruments simultaneously.

Covers the multi-instrument plumbing:

1. MNQ streams after it is explicitly joined (no default instrument).
2. When a client joins a second instrument (MES), the ``StreamCoordinator``
   activator makes the data source send a per-instrument ``subscribe`` and
   ``refresh_request`` to the platform.
3. MES history and live bars land in the MES per-pair cache / MES session and
   never pollute the MNQ cache.
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
    """Registry with MNQ and MES."""

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
    gateway = TradingGateway(e2e_logger, config=config)
    data_source = ZMQDataSource(e2e_logger, gateway=gateway)
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


def test_join_second_instrument_streams_alongside_first(
    fake_nt: FakeNinjaTrader,
    live_app_two_instruments: AppWiring,
) -> None:
    app = live_app_two_instruments
    data_source = app.data_source
    coordinator = app.coordinator
    assert coordinator is not None

    # (a) Join MNQ explicitly, connect the platform, and let MNQ reach STREAMING.
    coordinator.join_instrument("MNQ", "fake-sid-1")
    time.sleep(0.3)  # slow-joiner protection
    fake_nt.send_connect(pair="MNQ")
    _wait_until(
        lambda: _command_seen(fake_nt, "subscribe", "MNQ 09-26"),
        description="MNQ subscribe command",
    )
    fake_nt.send_history_batch(_build_history(60, "MNQ"))
    fake_nt.send_history_end(pair="MNQ")
    _wait_until(lambda: data_source.is_streaming, description="MNQ STREAMING")

    # (b) Simulate a browser joining the MES instrument room.
    coordinator.join_instrument("MES", "fake-sid-2")

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
    # per-pair cache only, and the connection state must be untouched.
    mes_history = _build_history(30, "MES")
    fake_nt.send_history_batch(mes_history, pair="MES")
    fake_nt.send_history_end(pair="MES")
    _wait_until(
        lambda: len(data_source.load_historical_bars("1m", pair="MES")) == 30,
        description="MES history in per-pair cache",
    )
    assert all(b["pair"] == "MNQ" for b in data_source._bars_by_pair.get("MNQ", []))
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
    assert all(b["pair"] == "MNQ" for b in data_source._bars_by_pair.get("MNQ", []))

    # (e) The MNQ stream is unaffected and keeps working.
    mnq_bars = data_source._bars_by_pair["MNQ"]
    mnq_bar = _make_bar(
        mnq_bars[-1]["time"] + 60,
        22000.0, 22010.0, 21990.0, 22005.0, 400, pair="MNQ",
    )
    fake_nt.send_bar(mnq_bar)
    _wait_until(
        lambda: any(b["time"] == mnq_bar["time"] for b in data_source._bars_by_pair["MNQ"]),
        description="live MNQ bar in MNQ cache",
    )


def test_second_instrument_refresh_flow_isolated_from_first(
    fake_nt: FakeNinjaTrader,
    live_app_two_instruments: AppWiring,
) -> None:
    """Regression: second instrument joining mid-stream.

    1. Its full refresh flow (refresh_start/batches/history_end) must emit a
       ``history_loaded`` for the second instrument even when its dedupe
       signature is identical to the first instrument's (same bar count and
       last-bar time — the weekend collision that emptied the MNQ chart).
    2. While the second instrument refreshes, the first instrument's bars must
       neither be diverted into the refresh buffer nor double-routed.
    3. The second session's readiness monitor is seeded with the current
       gateway connection state (CONNECTED, not DISCONNECTED).
    """
    app = live_app_two_instruments
    data_source = app.data_source
    coordinator = app.coordinator

    # Capture history_loaded emits at the socket layer.
    history_loaded_payloads: list[dict] = []
    orig_emit = app.socketio.emit

    def _emit_catcher(event, *args, **kwargs):
        if event == "history_loaded":
            history_loaded_payloads.append(args[0] if args else kwargs.get("data"))
        return orig_emit(event, *args, **kwargs)

    app.socketio.emit = _emit_catcher

    # Count route_bar deliveries for one specific MNQ live bar.
    mnq_route_count = {"n": 0}
    mnq_live_time: dict[str, int] = {}
    orig_route_bar = coordinator.route_bar

    def _route_bar_spy(bar):
        if bar.get("pair") == "MNQ" and bar.get("time") == mnq_live_time.get("t"):
            mnq_route_count["n"] += 1
        return orig_route_bar(bar)

    coordinator.route_bar = _route_bar_spy

    try:
        # (a) MNQ streams first.
        coordinator.join_instrument("MNQ", "fake-sid-1")
        time.sleep(0.3)  # slow-joiner protection
        fake_nt.send_connect(pair="MNQ")
        _wait_until(
            lambda: _command_seen(fake_nt, "subscribe", "MNQ 09-26"),
            description="MNQ subscribe command",
        )
        mnq_history = _build_history(60, "MNQ")
        fake_nt.send_history_batch(mnq_history, pair="MNQ")
        fake_nt.send_history_end(pair="MNQ")
        _wait_until(lambda: data_source.is_streaming, description="MNQ STREAMING")
        _wait_until(
            lambda: any(p and p.get("pair") == "MNQ" for p in history_loaded_payloads),
            description="history_loaded for MNQ",
        )

        # (b) Second client joins MES; its session monitor is seeded CONNECTED.
        coordinator.join_instrument("MES", "fake-sid-2")
        mes_session = coordinator.get_session("MES")
        assert mes_session is not None
        assert mes_session.readiness_monitor._state_machine.state.name == "CONNECTED"
        _wait_until(
            lambda: _command_seen(fake_nt, "refresh_request", "MES 09-26"),
            description="MES refresh_request command",
        )

        # (c) The platform starts the MES refresh. An MNQ live bar arriving
        # during it must stream normally: stored immediately, not buffered.
        fake_nt.send_refresh_start(pair="MES")
        mnq_live = _make_bar(
            mnq_history[-1]["time"] + 60,
            22000.0, 22010.0, 21990.0, 22005.0, 400, pair="MNQ",
        )
        mnq_live_time["t"] = mnq_live["time"]
        fake_nt.send_bar(mnq_live)
        _wait_until(
            lambda: any(b["time"] == mnq_live["time"] for b in data_source._bars_by_pair["MNQ"]),
            description="MNQ live bar stored during MES refresh",
        )
        assert "MNQ" not in data_source._refresh_buffer

        # (d) MES history with an IDENTICAL dedupe signature (same bar count,
        # same last-bar time) must still emit its own history_loaded.
        mes_history = [dict(b, pair="MES") for b in mnq_history]
        fake_nt.send_history_batch(mes_history, pair="MES")
        fake_nt.send_history_end(pair="MES")
        _wait_until(
            lambda: any(p and p.get("pair") == "MES" for p in history_loaded_payloads),
            description="history_loaded for MES despite identical signature",
        )

        # (e) The MNQ live bar was routed exactly once — the old shared
        # refresh buffer used to re-route it at the second pair's history_end.
        assert mnq_route_count["n"] == 1
    finally:
        app.socketio.emit = orig_emit
        coordinator.route_bar = orig_route_bar
