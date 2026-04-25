"""
Crash recovery tests.

Simulates app shutdown/restart during live trading and verifies that
strategy state is correctly restored from the database.

Uses real 1m bars from csvs/NQ_live.csv and real prod config to exercise
the full strategy pipeline.
"""
import csv
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict


from tests.fakes import (
    DummySocketIO,
    FakeLineRepository,
    FakeTradeRepository,
    FakeTradeExecutor,
    FakeAnalyticsReporter,
    FakeLogger,
)
from src.repositories.line_trigger_state_repository import InMemoryLineTriggerStateRepository
from src.services.trade_manager import TradeManager
from src.strategies.base_liquidity_strategy import StrategyOptions
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2
from src.prod_config import get_prod_strategy_options, get_prod_candle_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "csvs", "NQ_live.csv")
MNQ_TZ = ZoneInfo("America/Chicago")
MNQ_FMT = "%d/%m/%Y %H:%M:%S"


def load_bars(start_epoch: float, end_epoch: float) -> List[Dict]:
    """Load 1m bars from the MNQ CSV for the given UTC epoch range."""
    bars = []
    with open(CSV_PATH, newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        reader = csv.DictReader(f, dialect=dialect)
        for row in reader:
            ts = f"{row['Date']} {row['Time']}"
            dt = datetime.strptime(ts, MNQ_FMT).replace(tzinfo=MNQ_TZ)
            epoch = int(dt.astimezone(timezone.utc).timestamp())
            if epoch < start_epoch:
                continue
            if epoch >= end_epoch:
                break
            bars.append({
                "time": epoch,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row.get("Volume", 0)),
                "pair": "MNQ",
            })
    return bars


def make_recovery_strategy(
    trade_repo: FakeTradeRepository,
    trigger_state_repo: InMemoryLineTriggerStateRepository,
    options_override: StrategyOptions = None,
):
    """Build a LiquidityStrategyV2 with prod config and shared repos."""
    sio = DummySocketIO()
    lr = FakeLineRepository()
    te = FakeTradeExecutor()
    ana = FakeAnalyticsReporter()
    tm = TradeManager(
        trade_repository=trade_repo,
        socketio=sio,
        pair="MNQ",
        trade_executor=te,
        analytics=ana,
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
    )

    if options_override is not None:
        options = options_override
    else:
        options = get_prod_strategy_options(max_bounce=90.0, min_cross_depth=5.0)
        options.breakeven = None

    return LiquidityStrategyV2(
        min_stop_loss=10.0,
        max_bounce=90.0,
        socketio=sio,
        line_repository=lr,
        trade_repository=trade_repo,
        trade_manager=tm,
        extra_sl_space=0.0,
        fixed_stop_loss=20.0,
        timeframes=["3m", "5m", "15m", "30m", "1h"],
        options=options,
        candle_config=get_prod_candle_config(),
        sl_levels=[15.0, 20.0, 30.0, 40.0],
        sl_level_tolerance=3,
        min_cross_depth=5.0,
        rr_ratio=5.0,
        trigger_state_repo=trigger_state_repo,
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
    )


def _feed_until_trade_open(strat, bars, trade_repo, start_idx=0):
    """Feed bars until a trade opens. Returns the bar index."""
    pre = len(trade_repo.inserted)
    for i in range(start_idx, len(bars)):
        strat.on_raw_bar(bars[i])
        if len(trade_repo.inserted) > pre:
            return i
    raise AssertionError("No trade was opened")


def _feed_until_sl(strat, bars, trade_repo, start_idx=0):
    """Feed bars until a trade closes with negative result. Returns bar index."""
    pre = len(trade_repo.closed)
    for i in range(start_idx, len(bars)):
        strat.on_raw_bar(bars[i])
        if len(trade_repo.closed) > pre and trade_repo.closed[-1]["result"] < 0:
            return i
    raise AssertionError("No SL was hit")


def _utc_epoch(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


def _utc_dt(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


# Scenario: MNQ 2025-05-01
# Lines: 20046.00, 20198.25 (at 01:00Z)
# Trade 1: short entry=20041.50, sl=20056.50, tp=19966.50 → SL hit
# Trade 2 (reentry): short entry=20038.00, sl=20053.00, tp=19963.00 → TP hit
MAY01_BARS = None

def _may01_bars():
    global MAY01_BARS
    if MAY01_BARS is None:
        MAY01_BARS = load_bars(_utc_epoch(2025, 5, 1, 6), _utc_epoch(2025, 5, 1, 16))
    return MAY01_BARS

MAY01_LINE_TS = _utc_epoch(2025, 5, 1, 1)

# Scenario: MNQ 2025-05-08
# Lines: 20123.75, 20292.00 (at 06:00Z)
# Trade 1: long entry=20132.75, sl=20102.75, tp=20282.75 → SL hit
# Trade 2 (reentry): long entry=20129.25, sl=20114.25, tp=20204.25
MAY08_BARS = None

def _may08_bars():
    global MAY08_BARS
    if MAY08_BARS is None:
        MAY08_BARS = load_bars(_utc_epoch(2025, 5, 8, 6), _utc_epoch(2025, 5, 8, 16))
    return MAY08_BARS

MAY08_LINE_TS = _utc_epoch(2025, 5, 8, 6)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCrashRecoveryReentry:
    """Crash right after an SL hit — re-entry must fire after restart."""

    def test_crash_after_sl_short_reentry(self):
        """MNQ 2025-05-01: short trade SL'd, crash → short re-entry fires."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        # PHASE 1: Run until SL
        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        crash_idx = _feed_until_sl(strat1, all_bars, trade_repo)

        assert trade_repo.inserted[0]["entry"] == 20041.50
        assert trade_repo.inserted[0]["stop_loss"] == 20056.50
        assert len(strat1._reentry_opportunities) == 1

        # PHASE 2: Crash & recover
        del strat1
        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat2.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        strat2.restore_trigger_states("MNQ")
        strat2.restore_open_trades()
        strat2.restore_reentry_opportunities("MNQ", reference_time=_utc_dt(all_bars[crash_idx]["time"]))

        assert len(strat2._reentry_opportunities) == 1
        assert strat2._reentry_opportunities[0]["direction"] == "short"
        assert strat2._reentry_opportunities[0]["level"] == 20046.00

        # PHASE 3: Feed remaining → re-entry fires
        pre = len(trade_repo.inserted)
        for bar in all_bars[crash_idx + 1:]:
            strat2.on_raw_bar(bar)
            if len(trade_repo.inserted) > pre:
                break

        reentry = trade_repo.inserted[-1]
        assert reentry["params"]["is_reentry"] is True
        assert reentry["entry"] == 20038.00
        assert reentry["stop_loss"] == 20053.00
        assert reentry["take_profit"] == 19963.00

    def test_crash_after_sl_long_reentry(self):
        """MNQ 2025-05-08: long trade SL'd, crash → long re-entry fires."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may08_bars()

        # PHASE 1
        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20123", 20123.75, creation_timestamp=MAY08_LINE_TS)
        strat1.add_strategy_line("line-20292", 20292.00, creation_timestamp=MAY08_LINE_TS)
        crash_idx = _feed_until_sl(strat1, all_bars, trade_repo)

        assert trade_repo.inserted[0]["entry"] == 20132.75
        assert trade_repo.inserted[0]["type"] == "long"
        assert len(strat1._reentry_opportunities) == 1

        # PHASE 2
        del strat1
        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.add_strategy_line("line-20123", 20123.75, creation_timestamp=MAY08_LINE_TS)
        strat2.add_strategy_line("line-20292", 20292.00, creation_timestamp=MAY08_LINE_TS)
        strat2.restore_trigger_states("MNQ")
        strat2.restore_open_trades()
        strat2.restore_reentry_opportunities("MNQ", reference_time=_utc_dt(all_bars[crash_idx]["time"]))

        assert len(strat2._reentry_opportunities) == 1
        assert strat2._reentry_opportunities[0]["direction"] == "long"

        # PHASE 3
        pre = len(trade_repo.inserted)
        for bar in all_bars[crash_idx + 1:]:
            strat2.on_raw_bar(bar)
            if len(trade_repo.inserted) > pre:
                break

        reentry = trade_repo.inserted[-1]
        assert reentry["params"]["is_reentry"] is True
        assert reentry["entry"] == 20129.25
        assert reentry["stop_loss"] == 20099.25
        assert reentry["take_profit"] == 20279.25

    def test_reentry_trade_reaches_tp_after_crash(self):
        """MNQ 2025-05-01: crash after initial SL, re-entry fires AND reaches TP."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        # PHASE 1: SL hit
        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        crash_idx = _feed_until_sl(strat1, all_bars, trade_repo)
        del strat1

        # PHASE 2: Recover
        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat2.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        strat2.restore_trigger_states("MNQ")
        strat2.restore_open_trades()
        strat2.restore_reentry_opportunities("MNQ", reference_time=_utc_dt(all_bars[crash_idx]["time"]))

        # PHASE 3: Feed all remaining bars — re-entry should fire AND reach TP
        for bar in all_bars[crash_idx + 1:]:
            strat2.on_raw_bar(bar)

        assert len(trade_repo.inserted) == 2
        assert len(trade_repo.closed) == 2
        # Re-entry trade reached TP (positive result)
        reentry_close = trade_repo.closed[-1]
        assert reentry_close["result"] > 0
        assert reentry_close["exit_price"] == 19963.00


class TestCrashRecoveryOpenTrade:
    """Crash while a trade is open — trade must survive."""

    def test_open_trade_restored_after_crash(self):
        """MNQ 2025-05-01: crash while initial trade is open, trade restored in strategy."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        # PHASE 1: Run until trade opens
        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        open_idx = _feed_until_trade_open(strat1, all_bars, trade_repo)

        assert len(strat1.open_trades) == 1
        assert trade_repo.inserted[0]["entry"] == 20041.50

        # PHASE 2: Crash & recover (trade still open — no close yet)
        assert len(trade_repo.closed) == 0
        del strat1

        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat2.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        strat2.restore_trigger_states("MNQ")
        strat2.restore_open_trades()

        # Trade is back in strategy's open_trades
        assert len(strat2.open_trades) == 1
        assert strat2.open_trades[0]["entry"] == 20041.50
        assert strat2.open_trades[0]["status"] == "open"

        # PHASE 3: Continue — trade should eventually close (SL or TP)
        for bar in all_bars[open_idx + 1:]:
            strat2.on_raw_bar(bar)
            if trade_repo.closed:
                break

        assert len(trade_repo.closed) == 1

    def test_open_trade_blocks_duplicate_entry(self):
        """After restoring an open trade, open_trades_limit filter blocks new entries."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        # PHASE 1: Open a trade
        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        open_idx = _feed_until_trade_open(strat1, all_bars, trade_repo)
        assert len(trade_repo.inserted) == 1
        del strat1

        # PHASE 2: Recover, add extra line
        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat2.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        strat2.add_strategy_line("line-extra", 20100.00, creation_timestamp=MAY01_LINE_TS)
        strat2.restore_trigger_states("MNQ")
        strat2.restore_open_trades()
        assert len(strat2.open_trades) == 1

        # PHASE 3: Feed some bars — no second trade should open
        for bar in all_bars[open_idx + 1: open_idx + 100]:
            strat2.on_raw_bar(bar)

        assert len(trade_repo.inserted) == 1


class TestCrashRecoveryTriggerState:
    """Crash mid-trigger-evaluation — direction/extreme/trigger state must survive."""

    def test_line_direction_and_extreme_survive_crash(self):
        """Line direction and extreme are persisted and restored after crash."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)

        # Feed bars until direction is latched on at least one line
        for bar in all_bars:
            strat1.on_raw_bar(bar)
            line = strat1.strategy_lines.get("line-20046", {})
            if line.get("direction") is not None:
                break

        saved_dir = strat1.strategy_lines["line-20046"]["direction"]
        saved_ext = strat1.strategy_lines["line-20046"]["extreme"]
        assert saved_dir is not None

        # Crash & recover
        del strat1
        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat2.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)

        # Before restore: fresh state
        assert strat2.strategy_lines["line-20046"]["direction"] is None
        assert strat2.strategy_lines["line-20046"]["extreme"] == 0.0

        strat2.restore_trigger_states("MNQ")

        # After restore
        assert strat2.strategy_lines["line-20046"]["direction"] == saved_dir
        assert strat2.strategy_lines["line-20046"]["extreme"] == saved_ext

    def test_vat_regime_survives_crash(self):
        """VAT velocity regime (locked on first touch) persists across restart."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)

        # Feed bars until vat_regime is set
        for bar in all_bars:
            strat1.on_raw_bar(bar)
            line = strat1.strategy_lines.get("line-20046", {})
            if "vat_regime" in line:
                break

        saved_regime = strat1.strategy_lines["line-20046"]["vat_regime"]
        saved_velocity = strat1.strategy_lines["line-20046"]["vat_velocity"]

        # Crash & recover
        del strat1
        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat2.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        strat2.restore_trigger_states("MNQ")

        assert strat2.strategy_lines["line-20046"]["vat_regime"] == saved_regime
        assert strat2.strategy_lines["line-20046"]["vat_velocity"] == saved_velocity

    def test_multiple_lines_restored_independently(self):
        """Two lines at different stages are both restored correctly."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-A", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-B", 20198.25, creation_timestamp=MAY01_LINE_TS)

        # Feed 60 bars — enough for at least one line to get state
        for bar in all_bars[:60]:
            strat1.on_raw_bar(bar)

        state_a = dict(strat1.strategy_lines.get("line-A", {}))
        state_b = dict(strat1.strategy_lines.get("line-B", {}))
        assert state_a.get("direction") is not None or state_b.get("direction") is not None

        # Crash & recover
        del strat1
        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.add_strategy_line("line-A", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat2.add_strategy_line("line-B", 20198.25, creation_timestamp=MAY01_LINE_TS)
        strat2.restore_trigger_states("MNQ")

        for key in ("direction", "extreme"):
            assert strat2.strategy_lines["line-A"].get(key) == state_a.get(key), (
                f"line-A.{key}: expected {state_a.get(key)}, got {strat2.strategy_lines['line-A'].get(key)}"
            )
            assert strat2.strategy_lines["line-B"].get(key) == state_b.get(key), (
                f"line-B.{key}: expected {state_b.get(key)}, got {strat2.strategy_lines['line-B'].get(key)}"
            )


class TestCrashRecoveryEdgeCases:
    """Edge cases and negative tests."""

    def test_reentry_not_restored_when_disabled(self):
        """If reentry_after_sl=False, no re-entry opportunity is restored."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        crash_idx = _feed_until_sl(strat1, all_bars, trade_repo)
        del strat1

        # Recover with reentry DISABLED
        opts = get_prod_strategy_options(max_bounce=90.0, min_cross_depth=5.0)
        opts.breakeven = None
        opts.reentry_after_sl = False
        strat2 = make_recovery_strategy(trade_repo, tsr, options_override=opts)
        strat2.restore_reentry_opportunities("MNQ", reference_time=_utc_dt(all_bars[crash_idx]["time"]))

        assert len(strat2._reentry_opportunities) == 0

    def test_reentry_of_reentry_not_restored(self):
        """A re-entry trade that hits SL should NOT produce another re-entry."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        # Run full scenario: initial trade + re-entry
        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        for bar in all_bars:
            strat1.on_raw_bar(bar)

        reentry_trades = [t for t in trade_repo.inserted if t.get("params", {}).get("is_reentry")]
        assert len(reentry_trades) >= 1

        # Simulate: re-entry trade also hits SL
        rt = reentry_trades[0]
        last_time = all_bars[-1]["time"]
        already_closed = any(c["trade_id"] == rt["trade_id"] for c in trade_repo.closed)
        if not already_closed:
            trade_repo.close_trade(
                trade_id=rt["trade_id"],
                exit_price=rt["stop_loss"],
                exit_time=datetime.fromtimestamp(last_time, tz=timezone.utc),
                result=-1.0,
                result_type="SL",
            )
        del strat1

        # Recover — no re-entry-of-re-entry
        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.restore_reentry_opportunities("MNQ", reference_time=_utc_dt(last_time))

        # Every restored opportunity must come from a non-reentry source trade
        for opp in strat2._reentry_opportunities:
            source = [
                t for t in trade_repo.inserted
                if t.get("params", {}).get("line_level") == opp["level"]
                and t["type"] == opp["direction"]
                and not t.get("params", {}).get("is_reentry")
            ]
            assert len(source) >= 1, f"Opportunity at {opp['level']} has no non-reentry source"

    def test_old_sl_beyond_cutoff_not_restored(self):
        """SL trades older than 4 hours are not restored."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        strat1 = make_recovery_strategy(trade_repo, tsr)
        strat1.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat1.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)
        crash_idx = _feed_until_sl(strat1, all_bars, trade_repo)
        del strat1

        # Reference time 5 hours after SL — outside 4h window
        far_future = _utc_dt(all_bars[crash_idx]["time"] + 5 * 3600)
        strat2 = make_recovery_strategy(trade_repo, tsr)
        strat2.restore_reentry_opportunities("MNQ", reference_time=far_future)

        assert len(strat2._reentry_opportunities) == 0

    def test_clean_restart_no_state(self):
        """Restart with empty repos — restore methods are no-ops."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()

        strat = make_recovery_strategy(trade_repo, tsr)
        strat.add_strategy_line("line-test", 20000.00, creation_timestamp=0)

        strat.restore_trigger_states("MNQ")
        strat.restore_open_trades()
        strat.restore_reentry_opportunities("MNQ")

        assert len(strat.open_trades) == 0
        assert len(strat._reentry_opportunities) == 0
        assert strat.strategy_lines["line-test"]["direction"] is None


class TestResetPreserveTriggerState:
    """The reset(preserve_trigger_state=True) path used during NinjaTrader refresh."""

    def test_reset_preserves_trigger_state_in_repo(self):
        """reset(preserve=True) clears in-memory state but keeps trigger state in repo."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        strat = make_recovery_strategy(trade_repo, tsr)
        strat.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)

        # Feed bars until direction latches
        for bar in all_bars[:60]:
            strat.on_raw_bar(bar)

        line_state = strat.strategy_lines.get("line-20046")
        assert line_state is not None and line_state["direction"] is not None

        saved = tsr.load_all("MNQ")
        assert "line-20046" in saved
        saved_dir = saved["line-20046"]["direction"]

        # Reset with preserve=True
        strat.reset(preserve_trigger_state=True)
        assert len(strat.strategy_lines) == 0

        # Repo still has state
        assert tsr.load_all("MNQ")["line-20046"]["direction"] == saved_dir

    def test_reset_without_preserve_deletes_trigger_state(self):
        """reset(preserve=False) deletes trigger state from repo."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        strat = make_recovery_strategy(trade_repo, tsr)
        strat.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)

        for bar in all_bars[:60]:
            strat.on_raw_bar(bar)

        assert "line-20046" in tsr.load_all("MNQ")

        strat.reset(preserve_trigger_state=False)

        assert "line-20046" not in tsr.load_all("MNQ")

    def test_refresh_restores_trigger_state_after_warmup(self):
        """Refresh: reset(preserve) → re-add lines → warmup → restore_trigger_states."""
        trade_repo = FakeTradeRepository()
        tsr = InMemoryLineTriggerStateRepository()
        all_bars = _may01_bars()

        strat = make_recovery_strategy(trade_repo, tsr)
        strat.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)

        # Feed until direction latches
        latch_idx = None
        for idx, bar in enumerate(all_bars):
            strat.on_raw_bar(bar)
            if strat.strategy_lines["line-20046"].get("direction") is not None:
                latch_idx = idx
                break

        assert latch_idx is not None
        saved_dir = strat.strategy_lines["line-20046"]["direction"]

        # Simulate refresh
        strat.reset(preserve_trigger_state=True)
        strat.add_strategy_line("line-20046", 20046.00, creation_timestamp=MAY01_LINE_TS)
        strat.add_strategy_line("line-20198", 20198.25, creation_timestamp=MAY01_LINE_TS)

        # Warmup: replay bars
        for bar in all_bars[:latch_idx + 1]:
            strat.on_raw_bar(bar)

        # Restore (overlays persisted state)
        strat.restore_trigger_states("MNQ")

        # Direction should match pre-refresh state
        assert strat.strategy_lines["line-20046"]["direction"] == saved_dir
