"""Tests for SwingBounceDetector."""


from src.strategies.tsi_cross.bounce_detector import SwingBounceDetector


class TestSwingBounceDetector:
    def _make_bars(self, lows, highs, closes=None):
        closes = closes or highs
        return [
            {"open": closes[i], "high": highs[i], "low": lows[i], "close": closes[i]}
            for i in range(len(lows))
        ]

    def test_find_previous_low_bounce_finds_swing(self):
        lows = [100, 99, 98, 97, 96, 95, 94, 95, 96, 97]
        highs = [101, 100, 99, 98, 97, 96, 95, 96, 97, 98]
        bars = self._make_bars(lows, highs)

        detector = SwingBounceDetector(lookback=10, neighbor_bars=1)
        bounce = detector.find_previous_low_bounce(bars)
        # The swing low is at index 6 (low=94)
        assert bounce == 94.0

    def test_find_previous_high_bounce_finds_swing(self):
        lows = [100, 99, 98, 97, 96, 97, 98, 99, 100, 101]
        highs = [101, 100, 99, 98, 97, 98, 99, 100, 101, 102]
        bars = self._make_bars(lows, highs)

        detector = SwingBounceDetector(lookback=10, neighbor_bars=1)
        bounce = detector.find_previous_high_bounce(bars)
        # The swing high is at index 9 (high=102)
        assert bounce == 102.0

    def test_fallback_to_min_when_no_swing(self):
        lows = [100, 101, 102, 103, 104]
        highs = [101, 102, 103, 104, 105]
        bars = self._make_bars(lows, highs)

        detector = SwingBounceDetector(lookback=5, neighbor_bars=1)
        bounce = detector.find_previous_low_bounce(bars)
        assert bounce == 100.0

    def test_fallback_to_max_when_no_swing(self):
        lows = [100, 101, 102, 103, 104]
        highs = [101, 102, 103, 104, 105]
        bars = self._make_bars(lows, highs)

        detector = SwingBounceDetector(lookback=5, neighbor_bars=1)
        bounce = detector.find_previous_high_bounce(bars)
        assert bounce == 105.0

    def test_empty_bars_returns_zero(self):
        detector = SwingBounceDetector()
        assert detector.find_previous_low_bounce([]) == 0.0
        assert detector.find_previous_high_bounce([]) == 0.0
