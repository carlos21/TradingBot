"""End-to-end tests: trading windows + global open-trades cap (liquidity_v2).

Proves the semantics of the trading-windows / open-trades-cap change:

- ``TradingWindow`` enforces window membership + a per-window INITIAL-entry
  limit (``max_trades=1`` per window in these tests). It no longer caps
  concurrent open trades.
- ``open_trades_limit`` (installed BEFORE ``trading_windows`` in the prod
  options, value = catalog ``max_open_trades`` = 2 for MNQ) is the single
  global concurrent-open cap.

One synthetic 1m-bar session drives four setups across three test windows:

  Phase 1 (w1): long setup on line A (998)  -> trade A opens.
  Phase 2 (w2): long setup on line B (1004) -> trade B opens while A is open.
  Phase 3 (w3): long setup on line C (1002) -> BLOCKED by open_trades_limit
                (2 trades already open); the dead line is removed.
  Phase 4 (w3): crash stops out A and B (SL); short setup on line D (978)
                -> OPENS (cap freed; w3's slot was never consumed by C).

Timestamps: bars are generated directly at wall-clock-recent times (last bar
~30s before "now", as ZMQDataSource requires freshness), so the catalog
windows (01:00-07:59 / 08:00-15:30 NY) cannot be used — the wall-clock window
a bar lands in depends on when the test runs. Instead the strategy's
``trading_windows`` filter is replaced with a GENUINE
``trading_windows_filter([w1, w2, w3], "America/New_York")`` whose windows are
derived from the shifted bar timeline (each setup strictly inside its own
window, windows non-overlapping, ``max_trades=1``). The ``open_trades_limit``
filter is left untouched — it is the subject under test.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.infrastructure.gateway.datasource import ZMQDataSource
from src.strategies.entry_context import TradingWindow, trading_windows_filter
from tests.e2e.conftest import E2EHarness, _force_ready
from tests.e2e.test_liquidity_v2_e2e import _wait_for_trade_closed_in_repo

_NY = ZoneInfo("America/New_York")

# Line levels (well separated so trade->line association is unambiguous).
LEVEL_A = 998.0    # phase-1 long
LEVEL_B = 1004.0   # phase-2 long
LEVEL_C = 1002.0   # phase-3 long (blocked by the cap)
LEVEL_D = 978.0    # phase-4 short (after the crash)

LINE_A, LINE_B, LINE_C, LINE_D = "line_A", "line_B", "line_C", "line_D"

# Live-bar index map (see _live_bar_specs).
_IDX_DIP_A = 3
_IDX_P2B_START = 34   # first bar after the drift-up (line B seeded here)
_IDX_B_ENTRY_ZONE_END = 45
_IDX_P3_START = 56    # line C seeded here
_IDX_P3_END = 70
_IDX_P4A_START = 71   # first crash bar
_IDX_P4B_START = 80   # line D seeded here
_N_LIVE_BARS = 95

_STREAM_DELAY = 0.05  # minimum reliable inter-bar delay (see test_csv_scenarios_e2e)


# ---------------------------------------------------------------------------
# Synthetic bars
# ---------------------------------------------------------------------------

def _closes(*vals: float) -> list[tuple[float, None, None]]:
    return [(v, None, None) for v in vals]


def _live_bar_specs() -> list[tuple[float, float | None, float | None]]:
    """(close, low_override, high_override) per live 1m bar.

    Default bar: open = previous close, high/low = body +/- 0.3pts, so the
    velocity score stays tiny and the volatility regime locks to SLOW
    (a single 1m TSI cross confirms) everywhere.
    """
    specs: list[tuple[float, float | None, float | None]] = []
    # --- Phase 1 (0-22): flat, wick through A, drift up -> TSI cross, meander.
    specs += _closes(1000.0, 1000.0, 1000.0)                              # 0-2
    specs += [(999.3, 992.5, None)]                                       # 3  dip A (depth 5.5)
    specs += _closes(999.0, 998.8)                                        # 4-5 soft (TSI below signal)
    specs += _closes(999.3, 999.8, 1000.3, 1000.8, 1001.3, 1001.8)        # 6-11 rise -> entry A
    specs += _closes(1001.6, 1001.9, 1001.5, 1001.8, 1001.6, 1001.9,
                     1001.5, 1001.7, 1001.5, 1001.8, 1001.6)              # 12-22 meander (A open)
    specs += _closes(1001.8, 1001.6, 1001.9, 1001.7, 1001.9)              # 23-27 dead (window gap)
    # --- Phase 2a (28-33): drift up above line B's level (B seeded after).
    specs += _closes(1002.4, 1003.0, 1003.6, 1004.2, 1004.8, 1005.4)      # 28-33
    # --- Phase 2b (34-50): wick through B, drift up -> TSI cross, meander.
    specs += _closes(1005.6, 1005.3, 1005.7)                              # 34-36
    specs += [(1004.9, 998.5, None)]                                      # 37 dip B (depth 5.5)
    specs += _closes(1004.6, 1004.4)                                      # 38-39 soft
    specs += _closes(1004.9, 1005.4, 1005.9, 1006.4, 1006.9, 1007.4)      # 40-45 rise -> entry B
    specs += _closes(1007.2, 1007.5, 1007.1, 1007.4, 1007.2)              # 46-50 meander (A+B open)
    specs += _closes(1007.0, 1006.7, 1006.9, 1006.6, 1006.8)              # 51-55 dead (window gap)
    # --- Phase 3 (56-70): wick through C, rise -> TSI cross -> BLOCKED by cap.
    specs += _closes(1006.6, 1006.8, 1006.5)                              # 56-58
    specs += [(1002.7, 996.5, None)]                                      # 59 dip C (depth 5.5)
    specs += _closes(1002.4, 1002.2)                                      # 60-61 soft
    specs += _closes(1002.6, 1003.1, 1003.6, 1004.1, 1004.6)              # 62-66 rise -> C attempt
    specs += _closes(1004.4, 1004.6, 1004.4, 1004.6)                      # 67-70 flat
    # --- Phase 4a (71-79): crash through both stop-losses, then flat bottom.
    specs += [(999.0, 998.5, None), (994.0, 993.5, None),                 # 71-72
              (989.0, 988.0, None),                                       # 73 -> B stopped (SL ~990-992.5)
              (984.0, 982.5, None),                                       # 74 -> A stopped (SL ~984-987)
              (979.0, 977.5, None), (974.0, 972.5, None)]                 # 75-76
    specs += _closes(971.5, 971.2, 971.4)                                 # 77-79 bottom
    # --- Phase 4b (80-94): rally, wick above D, fall -> TSI cross down -> short D.
    specs += _closes(972.2, 973.0, 973.8, 974.6, 975.4, 976.2,
                     977.0, 977.6)                                        # 80-87 rally
    specs += [(977.9, None, 983.6)]                                       # 88 wick above D (depth 5.6)
    specs += _closes(977.2, 976.6)                                        # 89-90 soft
    specs += _closes(975.9, 975.2, 974.5)                                 # 91-93 fall -> entry D (short)
    specs += _closes(974.8)                                               # 94 tail (D stays open)
    assert len(specs) == _N_LIVE_BARS, f"expected {_N_LIVE_BARS} live bars, got {len(specs)}"
    return specs


def _specs_to_bars(specs: list[tuple[float, float | None, float | None]], t0: int) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    prev_close: float | None = None
    for i, (close, low, high) in enumerate(specs):
        o = prev_close if prev_close is not None else close
        h = high if high is not None else max(o, close) + 0.3
        lo = low if low is not None else min(o, close) - 0.3
        bars.append({
            "time": t0 + i * 60,
            "open": o,
            "high": h,
            "low": lo,
            "close": close,
            "volume": 100,
            "pair": "MNQ",
        })
        prev_close = close
    return bars


def _build_bars() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Flat warmup (TSI/volatility init, SLOW regime) + the live session.

    Timestamps are wall-clock-recent: the last live bar is ~30s before now so
    ZMQDataSource treats the stream as fresh (same trick as the CSV e2e test).
    """
    n_warmup = 130
    warmup_specs = [(1000.0, 999.8, 1000.2)] * n_warmup
    live_specs = _live_bar_specs()
    t_last = int(time.time()) - 30
    t_live0 = t_last - (len(live_specs) - 1) * 60
    t_warm0 = t_live0 - n_warmup * 60
    return _specs_to_bars(warmup_specs, t_warm0), _specs_to_bars(live_specs, t_live0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=_NY).strftime("%H:%M")


def _install_test_windows(strategy, live_bars: list[dict[str, Any]]) -> None:
    """Replace ONLY the trading_windows filter with genuine test windows.

    w1/w2/w3 are derived from the shifted bar timeline so each setup lands
    strictly inside its own window (with >=2.5min margin) and windows never
    overlap — regardless of the wall-clock time the test runs at.
    """
    t = [b["time"] for b in live_bars]
    windows = [
        TradingWindow(_hhmm(t[_IDX_DIP_A] - 150), _hhmm(t[14] + 150), max_trades=1),
        TradingWindow(_hhmm(t[_IDX_P2B_START] - 150), _hhmm(t[_IDX_B_ENTRY_ZONE_END] + 150), max_trades=1),
        TradingWindow(_hhmm(t[_IDX_P3_START] - 150), _hhmm(t[_N_LIVE_BARS - 1] + 150), max_trades=1),
    ]
    replacement = trading_windows_filter(windows, "America/New_York")
    for i, f in enumerate(strategy.entry_filters):
        if f.name == "trading_windows":
            strategy.entry_filters[i] = replacement
            return
    raise AssertionError("trading_windows filter not found in strategy.entry_filters")


def _wait_until(predicate, desc: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError(desc)


def _seed_line(strategy, line_id: str, level: float, creation_ts: float) -> None:
    strategy.add_strategy_line(id=line_id, level=level, creation_timestamp=creation_ts)


def _open_count(strategy) -> int:
    """Number of trades the open_trades_limit filter currently sees."""
    return sum(1 for t in strategy.open_trades if t.get("status") == "open")


def _order_opens(nt) -> list[dict[str, Any]]:
    return [c for c in nt.commands_received if c["msg_type"] == "order_open"]


def _trades_by_level(repo) -> dict[float, dict[str, Any]]:
    return {(t.get("params") or {}).get("line_level"): t for t in repo.inserted}


def _c_blocked_by_cap(strategy) -> bool:
    return any(
        e.get("event") == "FILTER_BLOCK"
        and e.get("line_id") == LINE_C
        and e.get("filter_name") == "open_trades_limit"
        for e in strategy.decision_logs
    )


def _epoch(dt_or_ts) -> float:
    return dt_or_ts.timestamp() if isinstance(dt_or_ts, datetime) else float(dt_or_ts)


def _exit_bar_epoch(trade: dict[str, Any], bars: list[dict[str, Any]]) -> float | None:
    """Bar-time at which the trade's SL/TP was first hit by a streamed bar.

    Repo exit timestamps are wall-clock (broker fill time) while entry_time is
    bar-time; to reason about concurrency in one consistent domain we
    reconstruct the exit in bar-time from the bars that caused the fill.
    """
    entry_t = _epoch(trade["entry_time"])
    sl = trade["stop_loss"]
    is_long = trade["type"] == "long"
    for b in bars:
        if b["time"] <= entry_t:
            continue
        if is_long and b["low"] <= sl:
            return float(b["time"])
        if not is_long and b["high"] >= sl:
            return float(b["time"])
    return None


def _max_concurrent(trades: list[dict[str, Any]], bars: list[dict[str, Any]]) -> int:
    """Peak number of simultaneously open trades, in the bar-time domain."""
    events: list[tuple[float, int]] = []
    for t in trades:
        events.append((_epoch(t["entry_time"]), +1))
        exit_t = _exit_bar_epoch(t, bars)
        if exit_t is not None:
            events.append((exit_t, -1))
    events.sort(key=lambda e: (e[0], e[1]))  # closes before opens on ties
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


# ---------------------------------------------------------------------------
# Session driver
# ---------------------------------------------------------------------------

def _start_session(harness: E2EHarness):
    """Send warmup history, install test windows, go LIVE. Returns (app, nt, live_bars)."""
    app, nt = harness.app, harness.nt
    if isinstance(app.data_source, ZMQDataSource):
        app.data_source.check_history_completeness = lambda bars=None: (True, "test")
    warmup_bars, live_bars = _build_bars()
    _install_test_windows(app.strategy, live_bars)

    nt.send_history_batch(warmup_bars)
    nt.send_history_end(pair="MNQ")
    _wait_until(lambda: app.data_source.is_streaming,
                "Data source never switched to LIVE after history_end", timeout=5.0)
    _force_ready(app)
    return app, nt, live_bars


def _drive_until_two_open(app, nt, live_bars) -> None:
    """Stream phases 1-2: trade A (w1) then trade B (w2) open concurrently."""
    strategy = app.strategy
    repo = app.trade_manager.trade_repository

    _seed_line(strategy, LINE_A, LEVEL_A, live_bars[0]["time"] - 60)
    nt.stream_bars(live_bars[0:28], delay_sec=_STREAM_DELAY)
    _wait_until(lambda: len(repo.inserted) == 1, "trade A never opened", timeout=15.0)

    nt.stream_bars(live_bars[28:34], delay_sec=_STREAM_DELAY)
    _seed_line(strategy, LINE_B, LEVEL_B, live_bars[_IDX_P2B_START]["time"] - 60)
    nt.stream_bars(live_bars[34:56], delay_sec=_STREAM_DELAY)
    _wait_until(
        lambda: len(repo.inserted) == 2 and _open_count(strategy) == 2,
        "trades A and B never open concurrently", timeout=15.0,
    )


def _drive_until_c_blocked(app, nt, live_bars) -> None:
    """Stream phase 3: line C's setup triggers but is vetoed by the cap."""
    strategy = app.strategy
    _seed_line(strategy, LINE_C, LEVEL_C, live_bars[_IDX_P3_START]["time"] - 60)
    nt.stream_bars(live_bars[_IDX_P3_START:_IDX_P3_END + 1], delay_sec=_STREAM_DELAY)
    _wait_until(lambda: _c_blocked_by_cap(strategy),
                "line C was never blocked by open_trades_limit", timeout=15.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTradingWindowsLimitsE2E:
    """Replay the synthetic session through the real ZMQ live path."""

    def test_two_concurrent_trades_across_windows(self, e2e_harness_auto: E2EHarness) -> None:
        """E1: one w1 trade + one w2 trade can be open at the same time (cap=2)."""
        app, nt, live_bars = _start_session(e2e_harness_auto)
        _drive_until_two_open(app, nt, live_bars)

        repo = app.trade_manager.trade_repository
        trades = _trades_by_level(repo)
        assert LEVEL_A in trades, f"no trade for line A; got levels {sorted(trades)}"
        assert LEVEL_B in trades, f"no trade for line B; got levels {sorted(trades)}"

        ta, tb = trades[LEVEL_A], trades[LEVEL_B]
        # A opens first and is still open when B opens (open intervals overlap).
        assert ta["entry_time"] <= tb["entry_time"]
        stored_a = repo.get_trade(ta["trade_id"])
        assert stored_a.exit_time is None or stored_a.exit_time >= tb["entry_time"]
        # The exact state the cap filter sees: two concurrent open trades.
        assert _open_count(app.strategy) == 2
        assert len(_order_opens(nt)) == 2

    def test_third_setup_blocked_while_two_open(self, e2e_harness_auto: E2EHarness) -> None:
        """E2: a 3rd setup while 2 trades are open is blocked by open_trades_limit."""
        app, nt, live_bars = _start_session(e2e_harness_auto)
        _drive_until_two_open(app, nt, live_bars)
        _drive_until_c_blocked(app, nt, live_bars)

        strategy = app.strategy
        repo = app.trade_manager.trade_repository
        trades = _trades_by_level(repo)
        # No trade was opened for line C (its window slot was never consumed —
        # the block came from the cap, which runs BEFORE the window filter).
        assert LEVEL_C not in trades, f"unexpected trade for line C: {trades.get(LEVEL_C)}"
        assert len(repo.inserted) == 2
        assert len(_order_opens(nt)) == 2
        # Hard-blocked (non-hold) lines are removed on evaluate.
        assert LINE_C not in strategy.strategy_lines

    def test_entry_allowed_after_trade_closes(self, e2e_harness_auto: E2EHarness) -> None:
        """E3: after A and B close (SL), a new setup in w3 opens; cap never exceeded."""
        app, nt, live_bars = _start_session(e2e_harness_auto)
        _drive_until_two_open(app, nt, live_bars)
        _drive_until_c_blocked(app, nt, live_bars)

        strategy = app.strategy
        repo = app.trade_manager.trade_repository
        trades = _trades_by_level(repo)
        ta_id = trades[LEVEL_A]["trade_id"]
        tb_id = trades[LEVEL_B]["trade_id"]

        # Crash: both trades stop out at their SLs.
        nt.stream_bars(live_bars[_IDX_P4A_START:_IDX_P4B_START], delay_sec=_STREAM_DELAY)
        closed_a = _wait_for_trade_closed_in_repo(repo, ta_id, timeout=10.0)
        closed_b = _wait_for_trade_closed_in_repo(repo, tb_id, timeout=10.0)
        assert closed_a.result_type == "SL", f"A closed with {closed_a.result_type}"
        assert closed_b.result_type == "SL", f"B closed with {closed_b.result_type}"
        _wait_until(lambda: _open_count(strategy) == 0,
                    "strategy still reports open trades after both SLs", timeout=10.0)

        # Cap freed: the line-D setup (also in w3) must open.
        _seed_line(strategy, LINE_D, LEVEL_D, live_bars[_IDX_P4B_START]["time"] - 60)
        nt.stream_bars(live_bars[_IDX_P4B_START:], delay_sec=_STREAM_DELAY)
        _wait_until(lambda: LEVEL_D in _trades_by_level(repo),
                    "trade D never opened after A and B closed", timeout=15.0)

        trades = _trades_by_level(repo)
        td = trades[LEVEL_D]
        # D opened after both SLs (bar-time domain).
        assert _epoch(td["entry_time"]) > _exit_bar_epoch(trades[LEVEL_A], live_bars)
        assert _epoch(td["entry_time"]) > _exit_bar_epoch(trades[LEVEL_B], live_bars)
        # At no moment were more than 2 trades open simultaneously.
        assert _max_concurrent(repo.inserted, live_bars) <= 2
