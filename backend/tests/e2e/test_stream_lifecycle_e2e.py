"""End-to-end tests for the streaming lifecycle (start / stop / restart).

Covers two recent production changes:

1. ``NinjaTraderBarAuditor._run_audit()`` skips silently (debug log, no error)
   when the gateway is not running or the data source is not streaming —
   previously it fired every 5 minutes even with streaming never started and
   logged ``[BarAuditor] Audit run failed: ...`` errors.
2. ``TradingGateway.on()`` / ``on_connection_change()`` are idempotent, so
   ``ZMQDataSource.start()`` after ``stop()`` cannot double-register callbacks
   (previously every tick/bar would have been delivered twice after a restart).

The tests drive the real HTTP routes (``POST /api/stream/start``,
``POST /api/stream/stop``) against the full live-mode wiring with
FakeNinjaTrader over real ZMQ sockets.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any

import pytest

from app_factory import AppWiring, Repositories, create_app
from src.infrastructure.database import database as db_module
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.gateway.executor import ZMQTradeExecutor
from src.infrastructure.gateway.gateway import GatewayConfig, TradingGateway
from src.infrastructure.market_closure_filter import MarketClosureFilter
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

# ---------------------------------------------------------------------------
# Recording logger
# ---------------------------------------------------------------------------


class _RecordingLogger(FakeLogger):
    """FakeLogger that records (level, message) pairs for assertions.

    Log records arrive from several background threads (ZMQ loops, timers),
    so appends are serialized.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.records: list[tuple[str, str]] = []

    def _record(self, level: str, message: str) -> None:
        with self._lock:
            self.records.append((level, message))

    def debug(self, message: str) -> None:
        self._record("debug", message)

    def info(self, message: str) -> None:
        self._record("info", message)

    def warning(self, message: str) -> None:
        self._record("warning", message)

    def error(self, message: str) -> None:
        self._record("error", message)

    def close(self) -> None:
        pass

    def messages(
        self,
        level: str | None = None,
        containing: str | None = None,
    ) -> list[str]:
        """Return recorded messages, optionally filtered by level/substring."""
        with self._lock:
            snapshot = list(self.records)
        return [
            msg
            for lv, msg in snapshot
            if (level is None or lv == level)
            and (containing is None or containing in msg)
        ]


@pytest.fixture
def e2e_logger() -> _RecordingLogger:
    """Override conftest's no-op ``e2e_logger`` with a recording one.

    The conftest fixture returns the no-op ``tests.fakes.FakeLogger``; these
    tests assert on log output, so every fixture that depends on
    ``e2e_logger`` (including conftest's ``fake_nt``) gets the recorder.
    """
    return _RecordingLogger()


# ---------------------------------------------------------------------------
# App fixture: full live wiring, NT account seeded, data source NOT started
# ---------------------------------------------------------------------------


@pytest.fixture
def live_app_idle(
    free_ports: dict[str, str],
    e2e_logger: _RecordingLogger,
):
    """Full TradingBot wiring in live_mode with real ZMQ sockets, NOT started.

    Mirrors conftest's ``live_app`` except:
      - seeds one NT account row so ``POST /api/stream/start`` passes the
        platform-lifecycle validation, and
      - never calls ``data_source.start()``; tests drive the lifecycle
        through the HTTP routes instead.
    """
    original_db = db_module.db
    test_db = _setup_in_memory_db()
    NtAccountRepository(db=test_db).upsert(
        name="Sim101",
        risk_usd=500.0,
        rr_ratio=3.0,
        live_enabled=True,
        instrument_symbols=["MNQ"],
    )

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
        session_end_time="23:59",  # late so tests control session-end explicitly
    )

    try:
        yield wiring
    finally:
        data_source.stop()
        gateway.stop()
        if wiring.bar_auditor is not None:
            wiring.bar_auditor.stop()
        _teardown_in_memory_db(original_db, test_db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_until(predicate, timeout: float = 5.0, description: str = "condition") -> None:
    """Poll *predicate* until it is true or the timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"Timed out ({timeout}s) waiting for {description}")


def _make_bar(
    t: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 100,
) -> dict[str, Any]:
    return {
        "time": t,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "pair": "MNQ",
    }


def _build_history(bar_count: int = 60) -> list[dict[str, Any]]:
    """Contiguous 1m bars ending at the current minute boundary (fresh)."""
    end = (int(time.time()) // 60) * 60
    start = end - (bar_count - 1) * 60
    return [
        _make_bar(start + i * 60, 21000.0 + i, 21010.0 + i, 20990.0 + i, 21005.0 + i)
        for i in range(bar_count)
    ]


def _connect_platform(nt: FakeNinjaTrader, gateway: TradingGateway, timeout: float = 5.0) -> None:
    """Send CONNECT and wait until the gateway reports the platform connected.

    After a backend stop/start cycle the fake's ZMQ sockets need a moment to
    re-establish the TCP connection to the re-bound Python sockets, so the
    CONNECT handshake is re-sent until it lands.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        nt.send_connect(pair="MNQ")
        time.sleep(0.15)
        if gateway.is_connected:
            return
    raise TimeoutError("platform never connected")


def _push_history(
    nt: FakeNinjaTrader,
    data_source: ZMQDataSource,
    bars: list[dict[str, Any]],
    timeout: float = 5.0,
) -> None:
    """Push a history batch + end marker and wait for STREAMING state."""
    nt.send_history_batch(bars)
    nt.send_history_end()
    _wait_until(
        lambda: data_source.is_streaming,
        timeout=timeout,
        description="data source to reach STREAMING",
    )


def _start_streaming(
    client,
    nt: FakeNinjaTrader,
    wiring: AppWiring,
    bars: list[dict[str, Any]],
) -> None:
    """Drive POST /api/stream/start -> connect -> history to reach STREAMING."""
    resp = client.post("/api/stream/start")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["status"] == "starting"

    gateway = wiring.data_source.gateway
    assert gateway is not None and gateway.is_running

    _connect_platform(nt, gateway)
    _push_history(nt, wiring.data_source, bars)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_idle_app_produces_no_auditor_errors(
    fake_nt: FakeNinjaTrader,
    live_app_idle: AppWiring,
    e2e_logger: _RecordingLogger,
) -> None:
    """With streaming never started, _run_audit() must skip silently."""
    data_source = live_app_idle.data_source
    gateway = data_source.gateway

    # Precondition: streaming was never started.
    assert not gateway.is_running
    assert not data_source.is_streaming

    auditor = live_app_idle.bar_auditor
    assert auditor is not None

    auditor._run_audit()
    time.sleep(0.2)  # give any (unexpected) ZMQ traffic a chance to arrive

    # The skip path ran (debug level only)...
    assert e2e_logger.messages(level="debug", containing="[BarAuditor] Skipping audit"), (
        "expected the silent-skip debug log from the auditor"
    )
    # ...and produced no ERROR/WARNING noise mentioning BarAuditor.
    noisy = [
        msg
        for level in ("error", "warning")
        for msg in e2e_logger.messages(level=level, containing="BarAuditor")
    ]
    assert noisy == [], f"unexpected auditor noise while idle: {noisy}"

    # No audit request ever reached the platform.
    audit_requests = [c for c in fake_nt.commands_received if c["msg_type"] == "audit_request"]
    assert audit_requests == [], f"unexpected audit requests: {audit_requests}"


def test_stop_then_restart_streams_bars_exactly_once(
    fake_nt: FakeNinjaTrader,
    live_app_idle: AppWiring,
    e2e_logger: _RecordingLogger,
) -> None:
    """Full start -> stop -> start lifecycle through the HTTP routes.

    Regression guard for double callback registration: after the restart, one
    streamed bar must be delivered exactly once.
    """
    data_source = live_app_idle.data_source
    gateway = data_source.gateway
    client = live_app_idle.app.test_client()

    # (a) Start streaming through the HTTP layer and reach STREAMING.
    history = _build_history(60)
    _start_streaming(client, fake_nt, live_app_idle, history)

    # (b) Stop through the HTTP layer; the gateway must really go down.
    resp = client.post("/api/stream/stop")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["status"] == "stopped"
    assert not gateway.is_running, "gateway must be stopped after /api/stream/stop"

    # (c) Start again through the HTTP layer.
    resp = client.post("/api/stream/start")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["status"] == "starting"
    assert gateway.is_running

    # Reconnect the platform and push history to reach STREAMING again.
    _connect_platform(fake_nt, gateway)
    _push_history(fake_nt, data_source, history)

    # (d) Push exactly one new completed bar; it must be processed once.
    before = data_source._stats["bars_received"]
    live_bar = _make_bar(history[-1]["time"] + 60, 22000.0, 22010.0, 21990.0, 22005.0, 500)
    fake_nt.send_bar(live_bar)

    _wait_until(
        lambda: data_source._stats["bars_received"] >= before + 1,
        timeout=3.0,
        description="live bar delivery after restart",
    )
    time.sleep(0.5)  # settle window: a duplicated delivery would land here

    assert data_source._stats["bars_received"] == before + 1, (
        f"bar delivered {data_source._stats['bars_received'] - before} times — "
        "callbacks were registered more than once"
    )
    assert data_source._duplicate_count == 0
    matching = [b for b in data_source._historical_bars if b["time"] == live_bar["time"]]
    assert len(matching) == 1

    # (e) No ERROR records throughout the whole lifecycle.
    errors = e2e_logger.messages(level="error")
    assert errors == [], f"unexpected ERROR logs during lifecycle: {errors}"


def test_audit_runs_while_streaming(
    fake_nt: FakeNinjaTrader,
    live_app_idle: AppWiring,
    e2e_logger: _RecordingLogger,
) -> None:
    """Once STREAMING, _run_audit() runs the round-trip and reports OK."""
    data_source = live_app_idle.data_source
    client = live_app_idle.app.test_client()

    # Start streaming with an empty history so the local cache contains
    # exactly the bars we stream below (audit compares cache vs platform).
    _start_streaming(client, fake_nt, live_app_idle, bars=[])

    # Stream a few bars; FakeNT mirrors them back on audit requests.
    base = (int(time.time()) // 60) * 60 - 180
    bars = [
        _make_bar(base, 21000.0, 21010.0, 20990.0, 21005.0, 100),
        _make_bar(base + 60, 21005.0, 21015.0, 20995.0, 21010.0, 200),
        _make_bar(base + 120, 21010.0, 21020.0, 21000.0, 21015.0, 300),
    ]
    for bar in bars:
        fake_nt.send_bar(bar)
        time.sleep(0.1)
    _wait_until(
        lambda: len(data_source.load_historical_bars("1m")) == len(bars),
        timeout=3.0,
        description="streamed bars to reach the data source cache",
    )

    auditor = live_app_idle.bar_auditor
    assert auditor is not None
    auditor._run_audit()  # blocks until the audit response arrives (~2s)

    assert e2e_logger.messages(level="error", containing="Audit run failed") == []
    assert e2e_logger.messages(level="warning", containing="Audit response timeout") == []
    ok_logs = e2e_logger.messages(level="info", containing="[BarAuditor] OK")
    assert ok_logs, f"expected an OK audit result; logs so far: {e2e_logger.records}"
