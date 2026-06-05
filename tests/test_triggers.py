"""Tests for src/strategies/triggers.py — TSI calculations, velocity, trigger functions."""

from unittest.mock import MagicMock

from src.strategies.liquidity_v2.config import CandleConfig
from src.domain.types import Direction
from src.strategies.indicators.tsi import calculate_ema as _calculate_ema
from src.strategies.liquidity_v2.triggers import (
    TsiCrossCondition,
    VelocityTriggerConfig,
    _build_tsi_context,
    _calculate_tsi_series,
    _calculate_velocity_score,
    _calculate_volatility_score,
    _check_tsi_condition,
    _get_history_with_gap_check,
    _handle_double_tsi_cross,
    _handle_single_tsi_cross,
    _process_tsi_rescue,
    double_5m_cross_trigger,
    make_velocity_adaptive_tsi_trigger,
    three_candle_reversal_trigger,
    trigger_with_timeframes,
    tsi_cross_trigger,

    wick_near_line_trigger,
)

# ─── Helper to build bars ───────────────────────────────────────────


def _bar(time=0, open_=100, high=102, low=98, close=101, pair="MNQ", tf="5m"):
    return {"time": time, "open": open_, "high": high, "low": low,
            "close": close, "volume": 100, "pair": pair, "tf": tf}


def _make_strategy_mock(history_map=None):
    """Create a mock strategy with configurable history per timeframe."""
    s = MagicMock()
    s.candle_config = CandleConfig()
    s.max_entry_distance = None
    _map = history_map or {}

    def get_history(tf, count):
        bars = _map.get(tf, [])
        return bars[-count:]

    s.get_history = MagicMock(side_effect=get_history)
    s.log_decision = MagicMock()
    s.remove_strategy_line = MagicMock()
    return s


# ─── TSI Math ────────────────────────────────────────────────────────


class TestCalculateEma:

    def test_single_value(self):
        result = _calculate_ema([42.0], 3)
        assert result == [42.0]

    def test_two_values(self):
        result = _calculate_ema([10.0, 20.0], 3)
        assert len(result) == 2
        # EMA(3): alpha=0.5, so second = 20*0.5 + 10*0.5 = 15
        assert abs(result[1] - 15.0) < 0.01

    def test_empty(self):
        assert _calculate_ema([], 3) == []


class TestCalculateTsiSeries:

    def test_returns_empty_when_insufficient_data(self):
        tsi, sig = _calculate_tsi_series([1, 2, 3], 6, 13, 4)
        assert tsi == []
        assert sig == []

    def test_returns_values_with_sufficient_data(self):
        # Generate 50 bars with uptrend
        closes = [100 + i * 0.5 for i in range(50)]
        tsi, sig = _calculate_tsi_series(closes, 6, 13, 4)
        assert len(tsi) > 0
        assert len(sig) > 0
        # TSI should be positive in uptrend
        assert tsi[-1] > 0

    def test_downtrend_negative_tsi(self):
        closes = [200 - i * 0.5 for i in range(50)]
        tsi, sig = _calculate_tsi_series(closes, 6, 13, 4)
        assert tsi[-1] < 0


class TestCalculateVelocityScore:

    def test_flat_market(self):
        bars = [_bar(close=100) for _ in range(30)]
        score = _calculate_velocity_score(bars, 10)
        assert score == 0.0

    def test_uptrend(self):
        bars = [_bar(open_=100 + i, close=100 + i + 1) for i in range(30)]
        score = _calculate_velocity_score(bars, 10)
        assert score > 0

    def test_downtrend(self):
        bars = [_bar(open_=200 - i, close=200 - i - 1) for i in range(30)]
        score = _calculate_velocity_score(bars, 10)
        assert score < 0

    def test_insufficient_history(self):
        bars = [_bar() for _ in range(3)]
        score = _calculate_velocity_score(bars, 10)
        assert score == 0.0


class TestCalculateVolatilityScore:

    def test_flat_market(self):
        bars = [_bar(close=100, high=100, low=100) for _ in range(30)]
        score = _calculate_volatility_score(bars, 10)
        assert score == 0.0

    def test_whipsaw_smoothed(self):
        """Bars that churn back and forth inside a 5m window are smoothed out."""
        bars = [_bar(open_=100, high=110, low=90, close=100) for _ in range(30)]
        score = _calculate_volatility_score(bars, 10)
        # 10 bars = 2 chunks of 5; each chunk range = 110 - 90 = 20
        # total = 40; score = 40 / 10 = 4.0
        assert score == 4.0

    def test_uptrend_with_ranges(self):
        bars = [_bar(open_=100 + i, high=102 + i, low=98 + i, close=101 + i) for i in range(30)]
        score = _calculate_volatility_score(bars, 10)
        # 10 bars = 2 chunks; chunk 1 range = 106-98 = 8, chunk 2 range = 111-103 = 8
        # total = 16; score = 16 / 10 = 1.6
        assert score == 1.6

    def test_insufficient_history(self):
        bars = [_bar() for _ in range(3)]
        score = _calculate_volatility_score(bars, 10)
        assert score == 0.0


# ─── Trigger Wrappers ───────────────────────────────────────────────


class TestTriggerWithTimeframes:

    def test_blocks_wrong_timeframe(self):
        inner = MagicMock(return_value="context")
        inner.__name__ = "test_trigger"
        wrapped = trigger_with_timeframes(inner, ["5m"])
        result = wrapped(None, "L1", {}, _bar(tf="1m"))
        assert result is None
        inner.assert_not_called()

    def test_allows_matching_timeframe(self):
        inner = MagicMock(return_value="context")
        inner.__name__ = "test_trigger"
        wrapped = trigger_with_timeframes(inner, ["5m"])
        result = wrapped(None, "L1", {}, _bar(tf="5m"))
        assert result == "context"
        inner.assert_called_once()


# ─── Wick Near Line Trigger ──────────────────────────────────────────


class TestWickNearLineTrigger:

    def test_no_direction_returns_none(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": None}
        result = wick_near_line_trigger(s, "L1", line, _bar())
        assert result is None

    def test_no_touch_returns_none(self):
        s = _make_strategy_mock()
        line = {"level": 200, "extreme": 205, "direction": "short"}
        # bar high=102, so doesn't reach 200
        result = wick_near_line_trigger(s, "L1", line, _bar())
        assert result is None

    def test_long_wick_triggers(self):
        s = _make_strategy_mock()
        line = {"level": 99, "extreme": 95, "direction": "long"}
        # bar with big lower wick touching the line
        bar = _bar(open_=100.5, high=101, low=98, close=100.8, tf="5m")
        # body = 0.3, range = 3, body_ratio = 0.1, lower_wick_ratio = (100.5-98)/3 = 0.83
        result = wick_near_line_trigger(s, "L1", line, bar)
        assert result is not None
        # Direction can be enum or string
        from src.domain.types import Direction
        assert result.direction == Direction.LONG or result.direction == "long"

    def test_body_too_big_rejects(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long"}
        # big body bar
        bar = _bar(open_=98, high=103, low=98, close=103, tf="5m")
        result = wick_near_line_trigger(s, "L1", line, bar)
        assert result is None


# ─── Double 5m Cross Trigger ────────────────────────────────────────


class TestDouble5mCrossTrigger:

    def test_long_full_cycle(self):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0, "direction": "long"}

        # Stage 0 -> 1: dip below
        bar1 = _bar(time=1, open_=99, high=101, low=97, close=98, tf="5m")
        assert double_5m_cross_trigger(s, "L1", line, bar1) is None
        assert line["d5_stage"] == 1

        # Stage 1 -> 2: close above
        bar2 = _bar(time=2, open_=99, high=102, low=99, close=101, tf="5m")
        assert double_5m_cross_trigger(s, "L1", line, bar2) is None
        assert line["d5_stage"] == 2

        # Stage 2 -> 3: second dip
        bar3 = _bar(time=3, open_=99, high=101, low=98, close=99.5, tf="5m")
        assert double_5m_cross_trigger(s, "L1", line, bar3) is None
        assert line["d5_stage"] == 3

        # Stage 3 -> Trigger: close above with open below (body cross)
        bar4 = _bar(time=4, open_=99.5, high=102, low=99, close=101, tf="5m")
        result = double_5m_cross_trigger(s, "L1", line, bar4)
        assert result is not None
        # Direction can be enum or string
        from src.domain.types import Direction
        assert result.direction == Direction.LONG or result.direction == "long"

    def test_short_full_cycle(self):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 105.0, "direction": "short"}

        bar1 = _bar(time=1, open_=101, high=103, low=99, close=102, tf="5m")
        assert double_5m_cross_trigger(s, "L1", line, bar1) is None
        assert line["d5_stage"] == 1

        bar2 = _bar(time=2, open_=101, high=101, low=98, close=99, tf="5m")
        assert double_5m_cross_trigger(s, "L1", line, bar2) is None
        assert line["d5_stage"] == 2

        bar3 = _bar(time=3, open_=100, high=101.5, low=99, close=100.5, tf="5m")
        assert double_5m_cross_trigger(s, "L1", line, bar3) is None
        assert line["d5_stage"] == 3

        # Trigger: close below with open above
        bar4 = _bar(time=4, open_=100.5, high=101, low=98, close=99, tf="5m")
        result = double_5m_cross_trigger(s, "L1", line, bar4)
        assert result is not None
        # Direction can be enum or string
        from src.domain.types import Direction
        assert result.direction == Direction.SHORT or result.direction == "short"

    def test_no_direction_returns_none(self):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0, "direction": None}
        result = double_5m_cross_trigger(s, "L1", line, _bar())
        assert result is None


# ─── Velocity Adaptive TSI Trigger ──────────────────────────────────


class TestVelocityAdaptiveTsiTrigger:

    def _make_crossing_history(self, direction="long", length=50):
        """Generate a history where TSI crosses signal at the end."""
        if direction == "long":
            # Downtrend then sharp uptick
            closes = [150 - i * 0.3 for i in range(length - 5)]
            closes.extend([closes[-1] + i * 2.0 for i in range(5)])
        else:
            # Uptrend then sharp downtick
            closes = [100 + i * 0.3 for i in range(length - 5)]
            closes.extend([closes[-1] - i * 2.0 for i in range(5)])
        return [_bar(time=i * 60, close=c, open_=c - 0.1, high=c + 1, low=c - 1, tf="1m")
                for i, c in enumerate(closes)]

    def test_no_direction_returns_none(self):
        trigger = make_velocity_adaptive_tsi_trigger()
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": None}
        assert trigger(s, "L1", line, _bar(tf="1m")) is None

    def test_no_tf_returns_none(self):
        trigger = make_velocity_adaptive_tsi_trigger()
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long"}
        bar = _bar()
        del bar["tf"]
        assert trigger(s, "L1", line, bar) is None

    def test_extreme_wrong_side_returns_none(self):
        """Long direction but extreme is above level -> no interaction."""
        trigger = make_velocity_adaptive_tsi_trigger()
        s = _make_strategy_mock({"1m": [_bar()] * 50})
        line = {"level": 100, "extreme": 105, "direction": "long"}
        assert trigger(s, "L1", line, _bar(tf="1m")) is None

    def test_regime_is_locked_on_first_touch(self):
        config = VelocityTriggerConfig(
            fast_threshold=999, slow_threshold=999, lookback=5,
            slow=[TsiCrossCondition("1m", 1)],
        )
        trigger = make_velocity_adaptive_tsi_trigger(config)
        history = [_bar(time=i * 60, close=100, tf="1m") for i in range(50)]
        s = _make_strategy_mock({"1m": history})
        line = {"level": 100, "extreme": 95, "direction": "long"}
        trigger(s, "L1", line, _bar(tf="1m"))
        assert "vat_regime" in line
        assert line["vat_regime"] == "SLOW"


# ─── Three Candle Reversal Trigger ──────────────────────────────────


class TestThreeCandleReversalTrigger:

    def test_no_direction_returns_none(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 105, "direction": None}
        assert three_candle_reversal_trigger(s, "L1", line, _bar()) is None

    def test_insufficient_history_returns_none(self):
        s = _make_strategy_mock({"5m": [_bar(), _bar()]})
        line = {"level": 100, "extreme": 105, "direction": "short"}
        assert three_candle_reversal_trigger(s, "L1", line, _bar(tf="5m")) is None

    def test_short_reversal_pattern(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 105, "direction": "short"}
        # C1: normal bar
        c1 = _bar(time=1, open_=99, high=101, low=98, close=100)
        # C2: touches line (low=99, high=101 crosses 100), small body, small upper wick
        # body=0.1/range=2 = 0.05 (< hammer_body_max_ratio=0.30)
        # upper_wick = 101 - max(100.05, 99.95) = 0.95 -> 0.95/2 = 0.475 (> hammer_nose_max_ratio=0.25) -- FAIL
        # Need: upper_wick / range < 0.25 => upper_wick < 0.5 for range=2
        # So open/close near the high: open_=100.6, close=100.5, high=101, low=99
        # body=0.1, range=2, body_ratio=0.05, upper=101-100.6=0.4, upper_ratio=0.2 OK
        c2 = _bar(time=2, open_=100.6, high=101, low=99, close=100.5)
        # C3: bearish (close < open)
        c3 = _bar(time=3, open_=101, high=101.5, low=99, close=99.5, tf="5m")
        s.get_history = MagicMock(return_value=[c1, c2, c3])
        result = three_candle_reversal_trigger(s, "L1", line, c3)
        assert result is not None
        # Direction can be enum or string
        from src.domain.types import Direction
        assert result.direction == Direction.SHORT or result.direction == "short"


# ─── Data Classes ───────────────────────────────────────────────────


class TestTsiCrossCondition:
    def test_defaults(self):
        cond = TsiCrossCondition("5m")
        assert cond.timeframe == "5m"
        assert cond.count == 1

    def test_custom_values(self):
        cond = TsiCrossCondition("1m", 2)
        assert cond.timeframe == "1m"
        assert cond.count == 2


class TestVelocityTriggerConfig:
    def test_defaults(self):
        cfg = VelocityTriggerConfig()
        assert cfg.fast_threshold == 5.0
        assert cfg.slow_threshold == 2.0
        assert cfg.lookback == 30
        assert len(cfg.fast) == 1
        assert cfg.fast[0].timeframe == "5m"
        assert cfg.fast[0].count == 2
        assert len(cfg.moderate) == 1
        assert cfg.moderate[0].timeframe == "3m"
        assert len(cfg.slow) == 1
        assert cfg.slow[0].timeframe == "1m"
        assert cfg.post_cross1_max_dist == 0.0

    def test_custom_regimes(self):
        cfg = VelocityTriggerConfig(
            fast=[TsiCrossCondition("5m", 2)],
            moderate=[TsiCrossCondition("3m", 1), TsiCrossCondition("1m", 1)],
            slow=[TsiCrossCondition("1m", 1)],
            post_cross1_max_dist=15.0,
        )
        assert len(cfg.moderate) == 2
        assert cfg.post_cross1_max_dist == 15.0


# ─── _build_tsi_context ─────────────────────────────────────────────


class TestBuildTsiContext:
    def _make_strategy_with_sl(self, sl_levels):
        s = _make_strategy_mock()
        s.sl_levels = sl_levels
        s.max_entry_distance = None
        return s

    def test_long_entry_too_far_above_line(self):
        s = self._make_strategy_with_sl([15.0, 20.0, 30.0])
        line = {"level": 100.0, "extreme": 95.0}
        bar = _bar(close=135.0)
        result = _build_tsi_context(s, "L1", line, bar, 100.0, Direction.LONG, 10.0, 5.0)
        assert result is None
        s.log_decision.assert_called_once()

    def test_short_entry_too_far_below_line(self):
        s = self._make_strategy_with_sl([15.0, 20.0, 30.0])
        line = {"level": 100.0, "extreme": 105.0}
        bar = _bar(close=65.0)
        result = _build_tsi_context(s, "L1", line, bar, 100.0, Direction.SHORT, -10.0, -5.0)
        assert result is None

    def test_long_valid_context(self):
        s = self._make_strategy_with_sl([15.0, 20.0, 30.0])
        line = {"level": 100.0, "extreme": 95.0}
        bar = _bar(close=102.0, low=98.0, high=103.0)
        result = _build_tsi_context(s, "L1", line, bar, 100.0, Direction.LONG, 10.0, 5.0)
        assert result is not None
        assert result.direction == Direction.LONG
        assert result.level == 100.0
        assert result.close == 102.0
        assert result.extreme == 95.0
        assert result.cross_depth == 5.0

    def test_short_valid_context(self):
        s = self._make_strategy_with_sl([15.0, 20.0, 30.0])
        line = {"level": 100.0, "extreme": 105.0}
        bar = _bar(close=98.0, low=97.0, high=108.0)
        result = _build_tsi_context(s, "L1", line, bar, 100.0, Direction.SHORT, -10.0, -5.0)
        assert result is not None
        assert result.direction == Direction.SHORT
        assert result.extreme == 108.0
        assert result.cross_depth == 8.0

    def test_no_sl_levels_allows_any_distance(self):
        s = _make_strategy_mock()
        s.sl_levels = None
        s.max_entry_distance = None
        line = {"level": 100.0, "extreme": 95.0}
        bar = _bar(close=200.0)
        result = _build_tsi_context(s, "L1", line, bar, 100.0, Direction.LONG, 10.0, 5.0)
        assert result is not None


# ─── _process_tsi_rescue ────────────────────────────────────────────


class TestProcessTsiRescue:
    def _history_from_closes(self, closes, tf="5m"):
        return [_bar(time=i * 300, close=c, open_=c - 0.1, high=c + 1, low=c - 1, tf=tf)
                for i, c in enumerate(closes)]

    def test_insufficient_history(self):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 10})
        line = {"level": 100.0, "extreme": 95.0}
        result = _process_tsi_rescue(s, "L1", line, _bar(tf="5m"), 100.0, Direction.LONG, 0, 0, "5m")
        assert result is None

    def test_long_reset_then_rescue(self, monkeypatch):
        # Reset pattern: t_curr < s_curr
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50})
        s.sl_levels = None
        line = {"level": 100.0, "extreme": 95.0}
        bar = _bar(tf="5m")
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, -1], [0, 0]),
        )
        result = _process_tsi_rescue(s, "L1", line, bar, 100.0, Direction.LONG, 0, 0, "5m")
        assert result is None
        assert line.get("tsi_reset_occurred") is True
        s.log_decision.assert_called_once()

        # Now rescue cross: prev_tsi <= prev_sig and curr_tsi > curr_sig
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([-1, 1], [0, 0]),
        )
        result = _process_tsi_rescue(s, "L1", line, bar, 100.0, Direction.LONG, 0, 0, "5m")
        assert result is not None
        assert result.direction == Direction.LONG
        assert line.get("tsi_reset_occurred") is False  # state reset

    def test_short_reset_then_rescue(self, monkeypatch):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50})
        s.sl_levels = None
        line = {"level": 100.0, "extreme": 105.0}
        bar = _bar(tf="5m")
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 1], [0, 0]),
        )
        result = _process_tsi_rescue(s, "L1", line, bar, 100.0, Direction.SHORT, 0, 0, "5m")
        assert result is None
        assert line.get("tsi_reset_occurred") is True

        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([1, -1], [0, 0]),
        )
        result = _process_tsi_rescue(s, "L1", line, bar, 100.0, Direction.SHORT, 0, 0, "5m")
        assert result is not None
        assert result.direction == Direction.SHORT

    def test_no_reset_no_rescue(self, monkeypatch):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0, "tsi_reset_occurred": False}
        bar = _bar(tf="5m")
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 1], [0, 0]),
        )
        result = _process_tsi_rescue(s, "L1", line, bar, 100.0, Direction.LONG, 0, 0, "5m")
        assert result is None
        # Reset should NOT occur because for LONG reset needs t_curr < s_curr
        assert line.get("tsi_reset_occurred") is False


# ─── _handle_single_tsi_cross ───────────────────────────────────────


class TestHandleSingleTsiCross:
    def _history_from_closes(self, closes, tf="5m"):
        return [_bar(time=i * 300, close=c, open_=c - 0.1, high=c + 1, low=c - 1, tf=tf)
                for i, c in enumerate(closes)]

    def test_long_cross_triggers(self):
        closes = [100.0 + (i % 3 - 1) * 0.5 for i in range(45)]
        closes.append(90.0)
        closes.append(100.0)
        s = _make_strategy_mock({"5m": self._history_from_closes(closes)})
        s.sl_levels = None
        line = {"level": 100.0, "extreme": 95.0}
        result = _handle_single_tsi_cross(s, "L1", line, _bar(tf="5m"), 100.0, Direction.LONG, "5m")
        assert result is not None
        assert result.direction == Direction.LONG

    def test_short_cross_triggers(self):
        closes = [100.0 + (i % 3 - 1) * 0.5 for i in range(45)]
        closes.append(100.0)
        closes.append(99.0)
        s = _make_strategy_mock({"5m": self._history_from_closes(closes)})
        s.sl_levels = None
        line = {"level": 100.0, "extreme": 105.0}
        result = _handle_single_tsi_cross(s, "L1", line, _bar(tf="5m"), 100.0, Direction.SHORT, "5m")
        assert result is not None
        assert result.direction == Direction.SHORT

    def test_no_cross_logs_gap(self):
        closes = [100.0 + (i % 3 - 1) * 0.1 for i in range(50)]
        s = _make_strategy_mock({"5m": self._history_from_closes(closes)})
        line = {"level": 100.0, "extreme": 95.0}
        result = _handle_single_tsi_cross(s, "L1", line, _bar(tf="5m"), 100.0, Direction.LONG, "5m")
        assert result is None
        s.log_decision.assert_called_once()
        args = s.log_decision.call_args[0]
        assert args[3] == "TSI_CHECK"

    def test_insufficient_history(self):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 10})
        line = {"level": 100.0, "extreme": 95.0}
        result = _handle_single_tsi_cross(s, "L1", line, _bar(tf="5m"), 100.0, Direction.LONG, "5m")
        assert result is None


# ─── _handle_double_tsi_cross ───────────────────────────────────────


class TestHandleDoubleTsiCross:
    def _history_from_closes(self, closes, tf="5m"):
        return [_bar(time=i * 300, close=c, open_=c - 0.1, high=c + 1, low=c - 1, tf=tf)
                for i, c in enumerate(closes)]

    def test_long_first_cross_sets_stage_one(self):
        closes = [100.0 + (i % 3 - 1) * 0.2 for i in range(46)]
        closes[-4] = 80.0
        closes[-3] = 96.0
        closes[-2] = 76.0
        closes[-1] = 82.0
        s = _make_strategy_mock({"5m": self._history_from_closes(closes)})
        line = {"level": 100.0, "extreme": 95.0}
        result = _handle_double_tsi_cross(s, "L1", line, _bar(tf="5m"), 100.0, Direction.LONG, "5m", "test_vat")
        assert result is None
        assert line.get("test_vat_stage") == 1
        assert line.get("test_vat_reset") is False

    def test_long_second_cross_after_reset_triggers(self):
        # Simulate stage 1 with reset already done
        s = _make_strategy_mock()
        s.sl_levels = None
        line = {"level": 100.0, "extreme": 95.0, "test_vat_stage": 1, "test_vat_reset": True}
        # History ending with cross up
        closes = [100.0 + (i % 3 - 1) * 0.5 for i in range(45)]
        closes.append(90.0)
        closes.append(100.0)
        s.get_history = MagicMock(return_value=self._history_from_closes(closes))
        result = _handle_double_tsi_cross(s, "L1", line, _bar(tf="5m"), 100.0, Direction.LONG, "5m", "test_vat")
        assert result is not None
        assert result.direction == Direction.LONG
        assert line.get("test_vat_stage") == 0
        assert line.get("test_vat_reset") is False

    def test_short_first_cross_sets_stage_one(self):
        closes = [100.0 + (i % 3 - 1) * 0.2 for i in range(46)]
        closes[-4] = 100.0
        closes[-3] = 80.0
        closes[-2] = 96.0
        closes[-1] = 76.0
        s = _make_strategy_mock({"5m": self._history_from_closes(closes)})
        line = {"level": 100.0, "extreme": 105.0}
        result = _handle_double_tsi_cross(s, "L1", line, _bar(tf="5m"), 100.0, Direction.SHORT, "5m", "test_vat")
        assert result is None
        assert line.get("test_vat_stage") == 1

    def test_max_dist_exceeded_removes_line(self):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0, "test_vat_stage": 1, "test_vat_reset": False}
        # Provide flat history so no cross happens, but bar close is far from line
        closes = [100.0 + (i % 3 - 1) * 0.1 for i in range(50)]
        s.get_history = MagicMock(return_value=self._history_from_closes(closes))
        bar = _bar(close=150.0, tf="5m")  # far above line for LONG
        result = _handle_double_tsi_cross(s, "L1", line, bar, 100.0, Direction.LONG, "5m", "test_vat", max_dist=20.0)
        assert result is None
        s.remove_strategy_line.assert_called_once_with("L1")
        assert line.get("test_vat_stage") == 0

    def test_insufficient_history(self):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 10})
        line = {"level": 100.0, "extreme": 95.0}
        result = _handle_double_tsi_cross(s, "L1", line, _bar(tf="5m"), 100.0, Direction.LONG, "5m", "test_vat")
        assert result is None


# ─── _check_tsi_condition ───────────────────────────────────────────


class TestCheckTsiCondition:
    def test_wrong_timeframe_returns_none(self):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0}
        bar = _bar(tf="3m")
        cond = TsiCrossCondition("5m", 1)
        result = _check_tsi_condition(s, "L1", line, bar, 100.0, Direction.LONG, cond)
        assert result is None

    def test_count_one_delegates_to_single(self, monkeypatch):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0}
        bar = _bar(tf="5m")
        cond = TsiCrossCondition("5m", 1)
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._handle_single_tsi_cross",
            lambda *args, **kwargs: "single_result",
        )
        result = _check_tsi_condition(s, "L1", line, bar, 100.0, Direction.LONG, cond)
        assert result == "single_result"

    def test_count_two_delegates_to_double(self, monkeypatch):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0}
        bar = _bar(tf="5m")
        cond = TsiCrossCondition("5m", 2)
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._handle_double_tsi_cross",
            lambda *args, **kwargs: "double_result",
        )
        result = _check_tsi_condition(s, "L1", line, bar, 100.0, Direction.LONG, cond)
        assert result == "double_result"


# ─── tsi_cross_trigger ──────────────────────────────────────────────


class TestTsiCrossTrigger:
    def test_no_direction_returns_none(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": None}
        assert tsi_cross_trigger(s, "L1", line, _bar(tf="5m")) is None

    def test_stage_dead_returns_none(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long", "tsi_stage": -1}
        assert tsi_cross_trigger(s, "L1", line, _bar(tf="5m")) is None

    def test_interaction_ts_in_future(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long", "interaction_ts": 9999}
        assert tsi_cross_trigger(s, "L1", line, _bar(time=1, tf="5m")) is None

    def test_extreme_wrong_side_long(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 105, "direction": "long"}
        assert tsi_cross_trigger(s, "L1", line, _bar(tf="5m")) is None

    def test_extreme_wrong_side_short(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "short"}
        assert tsi_cross_trigger(s, "L1", line, _bar(tf="5m")) is None

    def test_no_tf_returns_none(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long"}
        bar = _bar()
        del bar["tf"]
        assert tsi_cross_trigger(s, "L1", line, bar) is None

    def test_insufficient_history(self):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 10})
        line = {"level": 100, "extreme": 95, "direction": "long"}
        assert tsi_cross_trigger(s, "L1", line, _bar(tf="5m")) is None

    def test_stage0_long_cross_triggers(self, monkeypatch):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50, "1m": [_bar(tf="1m")] * 10})
        s.sl_levels = None
        line = {"level": 100, "extreme": 95, "direction": "long"}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 1], [1, 0]),
        )
        result = tsi_cross_trigger(s, "L1", line, _bar(tf="5m"))
        assert result is not None
        assert result.direction == Direction.LONG

    def test_stage0_short_cross_triggers(self, monkeypatch):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50, "1m": [_bar(tf="1m")] * 10})
        s.sl_levels = None
        line = {"level": 100, "extreme": 105, "direction": "short"}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([1, 0], [0, 1]),
        )
        result = tsi_cross_trigger(s, "L1", line, _bar(tf="5m"))
        assert result is not None
        assert result.direction == Direction.SHORT

    def test_stage0_long_fast_move_goes_to_stage_one(self, monkeypatch):
        # 1m history with strong downtrend for fast move detection
        hist_1m = [_bar(time=i * 60, open_=100.0, close=100.0, tf="1m") for i in range(5)]
        hist_1m[-1]["close"] = 84.0  # velocity = (84 - 100) / 5 = -3.2
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50, "1m": hist_1m})
        line = {"level": 100, "extreme": 95, "direction": "long"}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 1], [1, 0]),
        )
        bar = _bar(tf="5m", low=90.0)
        result = tsi_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line.get("tsi_stage") == 1
        assert line.get("tsi_ref_price") == 90.0

    def test_stage0_short_fast_move_goes_to_stage_one(self, monkeypatch):
        hist_1m = [_bar(time=i * 60, open_=100.0, close=100.0, tf="1m") for i in range(5)]
        hist_1m[-1]["close"] = 116.0  # velocity = (116 - 100) / 5 = 3.2
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50, "1m": hist_1m})
        line = {"level": 100, "extreme": 105, "direction": "short"}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([1, 0], [0, 1]),
        )
        bar = _bar(tf="5m", high=110.0)
        result = tsi_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line.get("tsi_stage") == 1
        assert line.get("tsi_ref_price") == 110.0

    def test_stage1_long_invalidation(self, monkeypatch):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50})
        line = {"level": 100, "extreme": 95, "direction": "long", "tsi_stage": 1, "tsi_ref_price": 90.0}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 0], [0, 0]),
        )
        bar = _bar(tf="5m", close=50.0)  # far below line
        result = tsi_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line.get("tsi_stage") == -1

    def test_stage1_short_invalidation(self, monkeypatch):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50})
        line = {"level": 100, "extreme": 105, "direction": "short", "tsi_stage": 1, "tsi_ref_price": 110.0}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 0], [0, 0]),
        )
        bar = _bar(tf="5m", close=150.0)  # far above line
        result = tsi_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line.get("tsi_stage") == -1

    def test_stage1_long_sweep_advances_to_stage_two(self, monkeypatch):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50})
        line = {"level": 100, "extreme": 95, "direction": "long", "tsi_stage": 1, "tsi_ref_price": 90.0}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 0], [0, 0]),
        )
        bar = _bar(tf="5m", low=89.0, close=99.0)  # low < ref_price, close not invalidated
        result = tsi_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line.get("tsi_stage") == 2
        assert line.get("tsi_reset_occurred") is False

    def test_stage1_short_sweep_advances_to_stage_two(self, monkeypatch):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50})
        line = {"level": 100, "extreme": 105, "direction": "short", "tsi_stage": 1, "tsi_ref_price": 110.0}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 0], [0, 0]),
        )
        bar = _bar(tf="5m", high=111.0, close=101.0)
        result = tsi_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line.get("tsi_stage") == 2

    def test_stage2_long_invalidation(self, monkeypatch):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50})
        line = {"level": 100, "extreme": 95, "direction": "long", "tsi_stage": 2}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 0], [0, 0]),
        )
        bar = _bar(tf="5m", close=50.0)
        result = tsi_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line.get("tsi_stage") == -1

    def test_stage2_rescue_triggers(self, monkeypatch):
        s = _make_strategy_mock({"5m": [_bar(tf="5m")] * 50})
        s.sl_levels = None
        line = {"level": 100, "extreme": 95, "direction": "long", "tsi_stage": 2, "tsi_reset_occurred": True}
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([-1, 1], [0, 0]),
        )
        bar = _bar(tf="5m")
        result = tsi_cross_trigger(s, "L1", line, bar)
        assert result is not None
        assert result.direction == Direction.LONG
        assert line.get("tsi_stage") == 0


# ─── wick_near_line_trigger (additional coverage) ───────────────────


class TestWickNearLineTriggerAdditional:
    def test_short_wick_triggers(self):
        s = _make_strategy_mock()
        line = {"level": 101, "extreme": 105, "direction": "short"}
        # Big upper wick, small body, touching line
        bar = _bar(open_=100.5, high=103, low=100, close=100.2, tf="5m")
        result = wick_near_line_trigger(s, "L1", line, bar)
        assert result is not None
        assert result.direction == Direction.SHORT

    def test_short_wick_too_small_rejects(self):
        s = _make_strategy_mock()
        line = {"level": 101, "extreme": 105, "direction": "short"}
        bar = _bar(open_=100.5, high=101, low=99, close=100.2, tf="5m")
        result = wick_near_line_trigger(s, "L1", line, bar)
        assert result is None
        s.log_decision.assert_called_once()

    def test_long_wick_too_small_rejects(self):
        s = _make_strategy_mock()
        line = {"level": 99, "extreme": 95, "direction": "long"}
        bar = _bar(open_=100.5, high=101, low=99.5, close=100.2, tf="5m")
        result = wick_near_line_trigger(s, "L1", line, bar)
        assert result is None

    def test_zero_range_returns_none(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long"}
        bar = _bar(open_=100, high=100, low=100, close=100, tf="5m")
        result = wick_near_line_trigger(s, "L1", line, bar)
        assert result is None

    def test_direction_as_enum(self):
        s = _make_strategy_mock()
        line = {"level": 99, "extreme": 95, "direction": Direction.LONG}
        bar = _bar(open_=100.5, high=101, low=98, close=100.8, tf="5m")
        result = wick_near_line_trigger(s, "L1", line, bar)
        assert result is not None
        assert result.direction == Direction.LONG


# ─── three_candle_reversal_trigger (additional coverage) ────────────


class TestThreeCandleReversalTriggerAdditional:
    def test_long_reversal_pattern(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long"}
        c1 = _bar(time=1, open_=101, high=102, low=99, close=100)
        # C2: small body, small lower wick (nose), touches line
        c2 = _bar(time=2, open_=99.4, high=101, low=99, close=99.5)
        # C3: bullish (close > open)
        c3 = _bar(time=3, open_=99.5, high=102, low=99, close=101, tf="5m")
        s.get_history = MagicMock(return_value=[c1, c2, c3])
        result = three_candle_reversal_trigger(s, "L1", line, c3)
        assert result is not None
        assert result.direction == Direction.LONG

    def test_c2_does_not_touch_line(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 105, "direction": "short"}
        c1 = _bar(time=1, open_=99, high=101, low=98, close=100)
        c2 = _bar(time=2, open_=100.6, high=101, low=100.1, close=100.5)  # doesn't cross 100
        c3 = _bar(time=3, open_=101, high=101.5, low=99, close=99.5, tf="5m")
        s.get_history = MagicMock(return_value=[c1, c2, c3])
        result = three_candle_reversal_trigger(s, "L1", line, c3)
        assert result is None

    def test_short_c2_body_too_big(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 105, "direction": "short"}
        c1 = _bar(time=1, open_=99, high=101, low=98, close=100)
        # Big body bar touching line
        c2 = _bar(time=2, open_=98, high=102, low=98, close=102)
        c3 = _bar(time=3, open_=101, high=101.5, low=99, close=99.5, tf="5m")
        s.get_history = MagicMock(return_value=[c1, c2, c3])
        result = three_candle_reversal_trigger(s, "L1", line, c3)
        assert result is None

    def test_short_c2_nose_too_big(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 105, "direction": "short"}
        c1 = _bar(time=1, open_=99, high=101, low=98, close=100)
        # Small body but big upper wick (nose)
        c2 = _bar(time=2, open_=99.5, high=102, low=99, close=99.6)
        c3 = _bar(time=3, open_=101, high=101.5, low=99, close=99.5, tf="5m")
        s.get_history = MagicMock(return_value=[c1, c2, c3])
        result = three_candle_reversal_trigger(s, "L1", line, c3)
        assert result is None

    def test_long_c2_nose_too_big(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long"}
        c1 = _bar(time=1, open_=101, high=102, low=99, close=100)
        # Small body but big lower wick (nose for long)
        c2 = _bar(time=2, open_=100.4, high=101, low=98, close=100.5)
        c3 = _bar(time=3, open_=99.5, high=102, low=99, close=101, tf="5m")
        s.get_history = MagicMock(return_value=[c1, c2, c3])
        result = three_candle_reversal_trigger(s, "L1", line, c3)
        assert result is None

    def test_c3_wrong_color_short(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 105, "direction": "short"}
        c1 = _bar(time=1, open_=99, high=101, low=98, close=100)
        c2 = _bar(time=2, open_=100.6, high=101, low=99, close=100.5)
        c3 = _bar(time=3, open_=99.5, high=101, low=99, close=100.5, tf="5m")  # bullish
        s.get_history = MagicMock(return_value=[c1, c2, c3])
        result = three_candle_reversal_trigger(s, "L1", line, c3)
        assert result is None

    def test_c3_wrong_color_long(self):
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long"}
        c1 = _bar(time=1, open_=101, high=102, low=99, close=100)
        c2 = _bar(time=2, open_=99.4, high=101, low=99, close=99.5)
        c3 = _bar(time=3, open_=101, high=102, low=99, close=99.5, tf="5m")  # bearish
        s.get_history = MagicMock(return_value=[c1, c2, c3])
        result = three_candle_reversal_trigger(s, "L1", line, c3)
        assert result is None


# ─── double_5m_cross_trigger (additional coverage) ──────────────────


class TestDouble5mCrossTriggerAdditional:
    def test_no_trigger_without_body_cross_long(self):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0, "direction": "long", "d5_stage": 3}
        # close > lvl but open_ is NOT < lvl
        bar = _bar(time=4, open_=101, high=102, low=99, close=101, tf="5m")
        result = double_5m_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line["d5_stage"] == 3  # stays in stage 3

    def test_no_trigger_without_body_cross_short(self):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 105.0, "direction": "short", "d5_stage": 3}
        bar = _bar(time=4, open_=99, high=101, low=98, close=99, tf="5m")
        result = double_5m_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line["d5_stage"] == 3

    def test_direction_as_enum_not_supported(self):
        # double_5m_cross_trigger does NOT handle enum direction — verify it errors
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0, "direction": Direction.LONG}
        bar = _bar(time=1, open_=99, high=101, low=97, close=98, tf="5m")
        try:
            double_5m_cross_trigger(s, "L1", line, bar)
            raise AssertionError("Expected AttributeError")
        except AttributeError:
            pass

    def test_stage0_no_advance_on_non_dip_long(self):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 95.0, "direction": "long", "d5_stage": 0}
        bar = _bar(time=1, open_=101, high=103, low=100.5, close=102, tf="5m")  # low >= lvl
        result = double_5m_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line["d5_stage"] == 0

    def test_stage0_no_advance_on_non_pop_short(self):
        s = _make_strategy_mock()
        line = {"level": 100.0, "extreme": 105.0, "direction": "short", "d5_stage": 0}
        bar = _bar(time=1, open_=99, high=99.5, low=97, close=98, tf="5m")  # high <= lvl
        result = double_5m_cross_trigger(s, "L1", line, bar)
        assert result is None
        assert line["d5_stage"] == 0


# ─── velocity_adaptive_tsi_trigger (additional coverage) ────────────


class TestVelocityAdaptiveTsiTriggerAdditional:
    def _make_crossing_history(self, direction="long", length=50, tf="1m"):
        """Generate a history where TSI crosses signal at the end."""
        if direction == "long":
            closes = [150 - i * 0.3 for i in range(length - 5)]
            closes.extend([closes[-1] + i * 2.0 for i in range(5)])
        else:
            closes = [100 + i * 0.3 for i in range(length - 5)]
            closes.extend([closes[-1] - i * 2.0 for i in range(5)])
        return [_bar(time=i * 60, close=c, open_=c - 0.1, high=c + 1, low=c - 1, tf=tf)
                for i, c in enumerate(closes)]

    def test_moderate_regime(self):
        config = VelocityTriggerConfig(
            fast_threshold=999, slow_threshold=-1, lookback=5,
            moderate=[TsiCrossCondition("3m", 1)],
        )
        trigger = make_velocity_adaptive_tsi_trigger(config)
        # 1m history with non-zero velocity so regime becomes MODERATE
        history = [_bar(time=i * 60, open_=100, close=100, tf="1m") for i in range(5)]
        history[-1]["close"] = 101.0
        # 3m history with cross at the end
        closes = [100.0 + (i % 3 - 1) * 0.5 for i in range(45)]
        closes.append(90.0)
        closes.append(100.0)
        hist_3m = [_bar(time=i * 300, close=c, open_=c - 0.1, high=c + 1, low=c - 1, tf="3m")
                   for i, c in enumerate(closes)]
        s = _make_strategy_mock({"1m": history, "3m": hist_3m})
        s.sl_levels = None
        line = {"level": 100, "extreme": 95, "direction": "long"}
        result = trigger(s, "L1", line, _bar(tf="3m"))
        assert result is not None
        assert line.get("vat_regime") == "MODERATE"

    def test_fast_regime(self):
        config = VelocityTriggerConfig(
            fast_threshold=-1, slow_threshold=-2, lookback=5,
            fast=[TsiCrossCondition("5m", 1)],
        )
        trigger = make_velocity_adaptive_tsi_trigger(config)
        history = [_bar(time=i * 60, open_=100, close=100, tf="1m") for i in range(5)]
        history[-1]["close"] = 101.0
        # 5m history with cross at the end
        closes = [100.0 + (i % 3 - 1) * 0.5 for i in range(45)]
        closes.append(90.0)
        closes.append(100.0)
        hist_5m = [_bar(time=i * 300, close=c, open_=c - 0.1, high=c + 1, low=c - 1, tf="5m")
                   for i, c in enumerate(closes)]
        s = _make_strategy_mock({"1m": history, "5m": hist_5m})
        s.sl_levels = None
        line = {"level": 100, "extreme": 95, "direction": "long"}
        result = trigger(s, "L1", line, _bar(tf="5m"))
        assert result is not None
        assert line.get("vat_regime") == "FAST"

    def test_interaction_ts_in_future(self):
        trigger = make_velocity_adaptive_tsi_trigger()
        s = _make_strategy_mock()
        line = {"level": 100, "extreme": 95, "direction": "long", "interaction_ts": 9999}
        assert trigger(s, "L1", line, _bar(time=1, tf="1m")) is None

    def test_regime_already_locked_reuses(self):
        config = VelocityTriggerConfig(
            fast_threshold=999, slow_threshold=999, lookback=5,
            slow=[TsiCrossCondition("1m", 1)],
        )
        trigger = make_velocity_adaptive_tsi_trigger(config)
        history = [_bar(time=i * 60, close=100, tf="1m") for i in range(50)]
        s = _make_strategy_mock({"1m": history})
        line = {"level": 100, "extreme": 95, "direction": "long", "vat_regime": "FAST"}
        # Even though velocity is slow, locked regime is FAST
        result = trigger(s, "L1", line, _bar(tf="1m"))
        # FAST regime has 5m condition, bar is 1m, so no match → None
        assert result is None
        assert line["vat_regime"] == "FAST"

    def test_post_cross1_max_dist_invalidates(self, monkeypatch):
        config = VelocityTriggerConfig(
            fast_threshold=999, slow_threshold=999, lookback=5,
            slow=[TsiCrossCondition("1m", 2)],
            post_cross1_max_dist=10.0,
        )
        trigger = make_velocity_adaptive_tsi_trigger(config)
        history = [_bar(time=i * 60, close=100, tf="1m") for i in range(50)]
        s = _make_strategy_mock({"1m": history})
        line = {"level": 100, "extreme": 95, "direction": "long"}
        # First call: first cross → stage 1
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 1], [1, 0]),
        )
        trigger(s, "L1", line, _bar(tf="1m"))
        assert line.get("vat_1m_2x_stage") == 1

        # Second call: price too far → invalidate
        bar = _bar(tf="1m", close=115.0)
        trigger(s, "L1", line, bar)
        s.remove_strategy_line.assert_called_once_with("L1")
        assert line.get("vat_1m_2x_stage") == 0

    def test_default_instance_exists(self):
        trigger = make_velocity_adaptive_tsi_trigger()
        assert trigger is not None
        assert trigger.__name__ == "velocity_adaptive_tsi_trigger"

    def test_multiple_conditions_tries_in_order(self, monkeypatch):
        config = VelocityTriggerConfig(
            fast_threshold=999, slow_threshold=999, lookback=5,
            slow=[TsiCrossCondition("1m", 2), TsiCrossCondition("1m", 1)],
        )
        trigger = make_velocity_adaptive_tsi_trigger(config)
        history = [_bar(time=i * 60, close=100, tf="1m") for i in range(50)]
        s = _make_strategy_mock({"1m": history})
        s.sl_levels = None
        line = {"level": 100, "extreme": 95, "direction": "long"}
        # count=2 will be checked first and not match (no stage set)
        # count=1 should match
        monkeypatch.setattr(
            "src.strategies.liquidity_v2.triggers._calculate_tsi_series",
            lambda *args: ([0, 1], [1, 0]),
        )
        result = trigger(s, "L1", line, _bar(tf="1m"))
        assert result is not None


class TestGetHistoryWithGapCheck:

    def test_no_gap_returns_full_history(self):
        history = [_bar(time=i * 180, close=100 + i) for i in range(35)]
        s = _make_strategy_mock({"3m": history})
        s._parse_tf_seconds = MagicMock(return_value=180)
        result = _get_history_with_gap_check(s, "3m", "L1", _bar(time=35 * 180))
        assert result is not None
        assert len(result) == 35

    def test_gap_detected_returns_post_gap_history(self):
        # Bars 0-9 are normal (0..1620), then a 600s gap, then bars 10-39 (30 post-gap bars)
        history = [_bar(time=i * 180 if i < 10 else 2220 + (i - 10) * 180, close=100 + i) for i in range(40)]
        s = _make_strategy_mock({"3m": history})
        s._parse_tf_seconds = MagicMock(return_value=180)
        result = _get_history_with_gap_check(s, "3m", "L1", _bar(time=history[-1]["time"]))
        assert result is not None
        # Post-gap should start from index 10
        assert len(result) == 30
        assert result[0]["time"] == history[10]["time"]

    def test_gap_with_insufficient_post_gap_returns_none(self):
        # Gap at index 5 (720 -> 1120 = 400s gap), only 20 bars after gap (need 30)
        history = [_bar(time=i * 180 if i < 5 else 1120 + (i - 5) * 180, close=100 + i) for i in range(25)]
        s = _make_strategy_mock({"3m": history})
        s._parse_tf_seconds = MagicMock(return_value=180)
        result = _get_history_with_gap_check(s, "3m", "L1", _bar(time=history[-1]["time"]))
        assert result is None

    def test_no_parse_tf_fallback_returns_full_history(self):
        history = [_bar(time=i * 180, close=100 + i) for i in range(35)]
        s = _make_strategy_mock({"3m": history})
        # No _parse_tf_seconds method
        del s._parse_tf_seconds
        result = _get_history_with_gap_check(s, "3m", "L1", _bar(time=35 * 180))
        assert result is not None
        assert len(result) == 35
