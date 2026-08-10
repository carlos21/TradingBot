"""End-to-end tests that replay real CSV bars through the ZMQ live path.

FakeNinjaTrader streams historical 1m bars to the TradingBot just like a real
NinjaTrader connector would. The strategy processes the bars naturally, generates
signals, and FakeNT auto-fills entries and exits. Tests assert that trade
outcomes match known expectations from an embedded scenario.
"""

from __future__ import annotations

import time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from dateutil import parser as dtparser

from src.infrastructure.gateway.datasource import ZMQDataSource
from src.strategies.entry_context import EntryFilter
from tests.e2e.conftest import E2EHarness, _force_ready
from tests.e2e.test_liquidity_v2_e2e import _wait_for_trade_closed_in_repo
from tests.fake_ninjatrader.csv_bar_loader import load_bars

# Embedded test scenario so the e2e test is not coupled to test_scenario.yaml,
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
        "sl": 28666.75,
        "tp": 28846.75,
        "reentry": {
            "entry": 28703.00,
            "sl": 28663.00,
            "tp": 28903.00,
        },
    },
    "show_tsi": False,
}


def _to_epoch(dt_str: str, tz_name: str = "America/Chicago") -> int:
    """Convert an ISO-like datetime string to UTC epoch seconds.

    The wall-clock time is treated as belonging to *tz_name*.
    """
    dt = dtparser.parse(dt_str)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    tz = ZoneInfo(tz_name)
    dt = dt.replace(tzinfo=tz)
    return int(dt.astimezone(ZoneInfo("UTC")).timestamp())


def _shift_bars_to_now(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shift bar timestamps so the last bar is ~30s ago (fresh enough for ZMQDataSource)."""
    if not bars:
        return []
    now = int(time.time())
    offset = now - bars[-1]["time"] - 30
    shifted = []
    for b in bars:
        sb = dict(b)
        sb["time"] = b["time"] + offset
        shifted.append(sb)
    return shifted


def _wait_for_any_trade(trade_manager, timeout: float = 10.0) -> dict[str, Any]:
    """Poll until at least one open trade appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if trade_manager.open_trades:
            return trade_manager.open_trades[0]
        time.sleep(0.01)
    raise TimeoutError("No trade opened within timeout")


class TestCSVScenarioReplay:
    """Replay historical CSV bars through ZMQ and verify strategy outcomes."""

    @pytest.fixture
    def test_scenario(self) -> dict[str, Any]:
        return dict(_TEST_SCENARIO)

    def test_csv_scenario_trade_opens_and_closes(self, e2e_harness_auto: E2EHarness, test_scenario: dict[str, Any]) -> None:
        """Stream Jun 11 2026 bars; strategy generates expected long signal.

        The scenario expects entry=28696.75, SL=28666.75, TP=28846.75.
        With the real CSV data the trade hits SL (price never reaches TP),
        so we assert the trade opens with correct parameters and then closes.
        """
        app = e2e_harness_auto.app
        nt = e2e_harness_auto.nt
        data_source = app.data_source

        # Bypass the real-time staleness check so historical replay works
        if isinstance(data_source, ZMQDataSource):
            data_source.check_history_completeness = lambda bars=None: (True, "test")

        # Time-shifted timestamps may fall outside trading hours; disable the
        # time_range/trading_windows filter so the strategy can evaluate
        # signals naturally.
        strategy = app.strategy
        for i, f in enumerate(strategy.entry_filters):
            if f.name in ("time_range", "trading_windows"):
                strategy.entry_filters[i] = EntryFilter(fn=lambda ctx: (True, "ok"), name=f.name)

        pair = test_scenario["pair"]
        start_ts = _to_epoch(test_scenario["start"])
        # 9h window — enough for the entry (~bar 200-300) and SL (~bar 400-500)
        # to fire.  Shorter windows (e.g. 8h) cause the trade to open but not
        # close before the bar stream ends.
        end_ts = start_ts + 9 * 3600
        warmup_start_ts = start_ts - 8 * 3600  # 8h warmup so all TFs are warm

        # ------------------------------------------------------------------
        # 1. Load bars from CSV
        # ------------------------------------------------------------------
        raw_bars = load_bars(
            "csvs/NQ_live.csv",
            pair=pair,
            start_time=warmup_start_ts,
            end_time=end_ts,
        )
        assert len(raw_bars) > 0, "No bars loaded from CSV"

        # Split first, then shift warmup bars so their last bar is ~30s ago
        warmup_bars_raw = [b for b in raw_bars if b["time"] < start_ts]
        live_bars_raw = [b for b in raw_bars if b["time"] >= start_ts]
        assert len(warmup_bars_raw) > 0, "No warmup bars"
        assert len(live_bars_raw) > 0, "No live bars"

        now = int(time.time())
        offset = now - warmup_bars_raw[-1]["time"] - 30
        warmup_bars = [dict(b, time=b["time"] + offset) for b in warmup_bars_raw]
        live_bars = [dict(b, time=b["time"] + offset) for b in live_bars_raw]
        time_shift = offset

        # ------------------------------------------------------------------
        # 2. Send warmup history
        # ------------------------------------------------------------------
        nt.send_history_batch(warmup_bars)
        nt.send_history_end(pair="MNQ")

        # Wait until the data source has processed HISTORY_END and the readiness
        # monitor has moved the state machine into WARMING_UP.  ZMQ delivery can
        # be delayed, so polling is more reliable than a fixed sleep.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if app.data_source.is_streaming:
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("Data source never switched to LIVE after history_end")

        # The CSV scenario uses higher timeframes (30m/1h) than the 8h warmup can
        # satisfy. Force the state machine to LIVE so the entry signal can fire.
        _force_ready(app)

        # ------------------------------------------------------------------
        # 3. Seed strategy lines AFTER warmup (so they aren't removed as stale)
        # ------------------------------------------------------------------
        for i, line_spec in enumerate(test_scenario.get("lines", [])):
            price = float(line_spec["price"])
            at_str = line_spec.get("at", test_scenario["start"])
            creation_ts = _to_epoch(at_str) + time_shift
            strategy.add_strategy_line(
                id=f"sc_line_{i}",
                level=price,
                creation_timestamp=creation_ts,
            )

        # ------------------------------------------------------------------
        # 4. Stream live bars — FakeNT auto-fills entries & exits
        # ------------------------------------------------------------------
        # 0.05s is the minimum reliable inter-bar delay.  Anything faster
        # (0.04s, 0.045s) causes the ZMQ subscriber to fall behind and the
        # trade either never opens or never closes.
        nt.stream_bars(live_bars, delay_sec=0.05)

        # Give ZMQ a moment to deliver any pending EXIT_FILL messages
        time.sleep(0.2)

        # ------------------------------------------------------------------
        # 5. Assertions
        # ------------------------------------------------------------------
        # The trade may open and close during streaming; check repository state.
        repo = app.trade_manager.trade_repository
        assert len(repo.inserted) >= 1, "Expected at least one trade to be inserted"

        trade = repo.inserted[0]
        trade_id = trade["trade_id"]
        assert trade["entry"] == pytest.approx(test_scenario["expect"]["entry"], abs=0.01)
        assert trade["stop_loss"] == pytest.approx(test_scenario["expect"]["sl"], abs=0.01)
        assert trade["take_profit"] == pytest.approx(test_scenario["expect"]["tp"], abs=0.01)

        # With auto-fill exits the trade should have closed
        closed = _wait_for_trade_closed_in_repo(
            app.trade_manager.trade_repository, trade_id, timeout=5.0
        )
        assert closed is not None, "Expected trade to close"
        assert closed.result_type in ("TP", "SL"), f"Expected TP or SL, got {closed.result_type}"

        # Verify FakeNT received the order_open command
        order_open_cmds = [c for c in nt.commands_received if c["msg_type"] == "order_open"]
        assert len(order_open_cmds) >= 1, "FakeNT should have received order_open"
