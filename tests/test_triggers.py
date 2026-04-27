"""Tests for src/strategies/triggers.py — TSI calculations, velocity, trigger functions."""

from unittest.mock import MagicMock

from src.strategies.triggers import (
    _calculate_ema,
    _calculate_tsi_series,
    _calculate_velocity_score,
    trigger_with_timeframes,
    make_velocity_adaptive_tsi_trigger,
    VelocityTriggerConfig,
    TsiCrossCondition,
    wick_near_line_trigger,
    three_candle_reversal_trigger,
    double_5m_cross_trigger,
)
from src.strategies.strategy_config import CandleConfig


# ─── Helper to build bars ───────────────────────────────────────────


def _bar(time=0, open_=100, high=102, low=98, close=101, pair="MNQ", tf="5m"):
    return {"time": time, "open": open_, "high": high, "low": low,
            "close": close, "volume": 100, "pair": pair, "tf": tf}


def _make_strategy_mock(history_map=None):
    """Create a mock strategy with configurable history per timeframe."""
    s = MagicMock()
    s.candle_config = CandleConfig()
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
        from src.types import Direction
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
        from src.types import Direction
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
        from src.types import Direction
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
        from src.types import Direction
        assert result.direction == Direction.SHORT or result.direction == "short"
