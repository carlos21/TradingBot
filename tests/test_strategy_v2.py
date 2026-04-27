"""Tests for src/strategies/liquidity_strategy_v2.py — V2 strategy: latching, aggregation, reset, history."""

from tests.conftest import make_bar, make_strategy
from tests.fakes import DummySocketIO, FakeLineRepository, FakeTradeRepository, FakeTradeExecutor, FakeAnalyticsReporter, FakeLogger
from src.services.trade_manager import TradeManager


def _deps():
    sio = DummySocketIO()
    lr = FakeLineRepository()
    tr = FakeTradeRepository()
    tm = TradeManager(
        tr, sio,
        trade_executor=FakeTradeExecutor(),
        analytics=FakeAnalyticsReporter(),
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
    )
    return sio, lr, tr, tm


class TestParseTimeframeSeconds:

    def test_minutes(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        assert strat._parse_tf_seconds("5m") == 300
        assert strat._parse_tf_seconds("1m") == 60
        assert strat._parse_tf_seconds("15m") == 900

    def test_hours(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        assert strat._parse_tf_seconds("1h") == 3600

    def test_days(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        assert strat._parse_tf_seconds("1d") == 86400


class TestGetHistory:

    def test_returns_empty_for_unknown_tf(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        assert strat.get_history("99m", 10) == []

    def test_returns_last_n_bars(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        for i in range(10):
            strat._tf_histories["1m"].append(make_bar(time=i * 60))
        result = strat.get_history("1m", 3)
        assert len(result) == 3
        assert result[0]["time"] == 7 * 60


class TestLogDecision:

    def test_appends_entry(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        strat.log_decision(1000, "5m", "L1", "TEST", "details")
        assert len(strat.decision_logs) == 1
        assert strat.decision_logs[0]["event"] == "TEST"


class TestReset:

    def test_clears_all_state(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        strat.add_strategy_line("L1", 100.0)
        strat.decision_logs.append({"test": True})
        strat.reset()
        assert len(strat.strategy_lines) == 0
        assert len(strat.decision_logs) == 0


class TestLatching:

    def test_long_latch_when_price_below_then_above(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=0.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)

        # Price below -> accumulate pending
        bar1 = make_bar(time=60, open_=98, high=99, low=97, close=97, pair="MNQ")
        strat.on_raw_bar(bar1)
        line = strat.strategy_lines["L1"]
        # Direction should latch to short since close < level and pending extreme (99) <= level
        assert line["direction"] == "short"

    def test_no_latch_when_price_equals_level(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=0.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)
        bar = make_bar(time=60, open_=100, high=100, low=100, close=100, pair="MNQ")
        strat.on_raw_bar(bar)
        assert strat.strategy_lines["L1"]["direction"] is None

    def test_long_latch_above_level(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=0.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)
        # Price above level -> latches long (low >= level or depth sufficient)
        bar = make_bar(time=60, open_=101, high=103, low=100, close=102, pair="MNQ")
        strat.on_raw_bar(bar)
        line = strat.strategy_lines["L1"]
        assert line["direction"] == "long"


class TestExtremeTracking:

    def test_short_extreme_updates(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=0.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)
        # Latch short
        bar1 = make_bar(time=60, open_=98, high=99, low=97, close=97, pair="MNQ")
        strat.on_raw_bar(bar1)
        # New higher high
        bar2 = make_bar(time=120, open_=98, high=103, low=97, close=98, pair="MNQ")
        strat.on_raw_bar(bar2)
        assert strat.strategy_lines["L1"]["extreme"] == 103

    def test_long_extreme_updates(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=0.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)
        # Latch long
        bar1 = make_bar(time=60, open_=101, high=103, low=100, close=102, pair="MNQ")
        strat.on_raw_bar(bar1)
        # New lower low
        bar2 = make_bar(time=120, open_=101, high=103, low=95, close=101, pair="MNQ")
        strat.on_raw_bar(bar2)
        assert strat.strategy_lines["L1"]["extreme"] == 95


class TestMaxBounceRemoval:

    def test_short_removed_on_max_bounce(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=0.0, max_bounce=10.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)
        # Latch short
        bar1 = make_bar(time=60, open_=98, high=99, low=97, close=97, pair="MNQ")
        strat.on_raw_bar(bar1)
        # Price goes above max bounce (100 + 10 = 110)
        bar2 = make_bar(time=120, open_=111, high=112, low=110, close=111, pair="MNQ")
        strat.on_raw_bar(bar2)
        assert "L1" not in strat.strategy_lines

    def test_long_removed_on_max_bounce(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=0.0, max_bounce=10.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)
        bar1 = make_bar(time=60, open_=101, high=103, low=100, close=102, pair="MNQ")
        strat.on_raw_bar(bar1)
        bar2 = make_bar(time=120, open_=89, high=90, low=88, close=89, pair="MNQ")
        strat.on_raw_bar(bar2)
        assert "L1" not in strat.strategy_lines


class TestResetTriggerState:

    def test_resets_d5_stage(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        line = {"direction": "long", "extreme": 95, "d5_stage": 3}
        strat._reset_trigger_state(line)
        assert line["d5_stage"] == 0

    def test_resets_tsi_stage(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        line = {"direction": "long", "extreme": 95, "tsi_stage": 2, "tsi_ref_price": 50.0}
        strat._reset_trigger_state(line)
        assert line["tsi_stage"] == 0
        assert line["tsi_ref_price"] == 0.0

    def test_clears_vat_regime(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm)
        line = {"direction": "long", "extreme": 95, "vat_regime": "SLOW", "vat_velocity": 1.5}
        strat._reset_trigger_state(line)
        assert "vat_regime" not in line
        assert "vat_velocity" not in line


class TestCreationTimestamp:

    def test_skips_lines_created_after_bar(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=0.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=500)
        bar = make_bar(time=100, close=97, pair="MNQ")
        strat.on_raw_bar(bar)


class TestLatchPending:

    def test_latch_pending_when_depth_insufficient(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=5.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)
        # Price goes below but high only touches 101 (1pt above line) — depth < 5
        bar1 = make_bar(time=60, open_=99, high=101, low=98, close=98, pair="MNQ")
        strat.on_raw_bar(bar1)
        line = strat.strategy_lines["L1"]
        assert line["direction"] is None
        assert line.get("_pending_dir") == "short"
        # Should have a LATCH_PENDING decision log
        pending_logs = [d for d in strat.decision_logs if d["event"] == "LATCH_PENDING"]
        assert len(pending_logs) == 1
        assert pending_logs[0]["direction"] == "short"
        assert "depth=" in pending_logs[0]["reason"]

    def test_latch_pending_long_when_depth_insufficient(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=5.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)
        # Price goes above but low only touches 99 (1pt below line) — depth < 5
        bar1 = make_bar(time=60, open_=101, high=102, low=99, close=101, pair="MNQ")
        strat.on_raw_bar(bar1)
        line = strat.strategy_lines["L1"]
        assert line["direction"] is None
        pending_logs = [d for d in strat.decision_logs if d["event"] == "LATCH_PENDING"]
        assert len(pending_logs) == 1
        assert pending_logs[0]["direction"] == "long"

    def test_latch_after_pending_depth_accumulates(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, min_cross_depth=5.0)
        strat.add_strategy_line("L1", 100.0, creation_timestamp=0)
        # First bar: high=101 (depth=1) -> pending
        bar1 = make_bar(time=60, open_=99, high=101, low=98, close=98, pair="MNQ")
        strat.on_raw_bar(bar1)
        assert strat.strategy_lines["L1"]["direction"] is None
        # Second bar: high=106 (depth=6) -> latch
        bar2 = make_bar(time=120, open_=98, high=106, low=97, close=97, pair="MNQ")
        strat.on_raw_bar(bar2)
        assert strat.strategy_lines["L1"]["direction"] == "short"
        latch_logs = [d for d in strat.decision_logs if d["event"] == "LATCH"]
        assert len(latch_logs) == 1
        assert latch_logs[0]["direction"] == "short"


class TestMultiTimeframeAggregation:

    def test_internal_timeframes_always_include_required(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, timeframes=["5m"])
        for tf in ["1m", "3m", "5m", "15m"]:
            assert tf in strat._internal_timeframes

    def test_aggregation_produces_bars(self):
        sio, lr, tr, tm = _deps()
        strat = make_strategy(sio, lr, tr, tm, timeframes=["1m"])
        # Feed 2 minutes of 1m bars (each in a different window)
        bar1 = make_bar(time=0, open_=100, close=101, high=102, low=99, pair="MNQ")
        bar2 = make_bar(time=60, open_=101, close=102, high=103, low=100, pair="MNQ")
        strat.on_raw_bar(bar1)
        strat.on_raw_bar(bar2)
        # After bar2, bar1's window should have produced an aggregated bar in 1m history
        hist = strat.get_history("1m", 10)
        assert len(hist) >= 1
