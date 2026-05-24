"""Tests for TsiAnalyzer."""

import pytest

from src.domain.types import Direction
from src.strategies.tsi_cross.tsi_analyzer import TsiAnalyzer


class TestTsiAnalyzer:
    def test_not_enough_data_returns_none(self):
        analyzer = TsiAnalyzer()
        result = analyzer.analyze([100.0] * 5)
        assert result.direction is None

    def test_bullish_cross_detected(self):
        """Build a price series that forces a bullish TSI cross.

        Pattern: decline first (TSI goes negative), then sharp rally
        (TSI crosses above signal on the first rally bar).
        """
        analyzer = TsiAnalyzer()
        closes = []
        p = 100.0
        for _ in range(30):
            p -= 0.5
            closes.append(p)
        for _ in range(30):
            p += 2.0
            closes.append(p)

        # Cross happens on bar 30 (first rally bar)
        result = analyzer.analyze(closes[:31])
        assert result.direction == Direction.LONG
        assert result.tsi_value > result.signal_value

    def test_bearish_cross_detected(self):
        """Build a price series that forces a bearish TSI cross."""
        analyzer = TsiAnalyzer()
        closes = []
        p = 100.0
        for _ in range(30):
            p += 0.5
            closes.append(p)
        for _ in range(30):
            p -= 2.0
            closes.append(p)

        # Cross happens on bar 30 (first drop bar)
        result = analyzer.analyze(closes[:31])
        assert result.direction == Direction.SHORT
        assert result.tsi_value < result.signal_value

    def test_no_cross_when_flat(self):
        """Flat prices should not produce a cross."""
        analyzer = TsiAnalyzer()
        closes = [100.0] * 60
        result = analyzer.analyze(closes)
        assert result.direction is None

    def test_no_cross_after_convergence(self):
        """After TSI and signal have both risen, no cross is reported."""
        analyzer = TsiAnalyzer()
        # Strong sustained uptrend — cross happens early, then both lines align
        closes = [100.0 + i * 2 for i in range(60)]
        result = analyzer.analyze(closes)
        # At the final bar there is no cross
        assert result.direction is None

    def test_confirmation_blocks_insufficient_history(self):
        """With confirmation_bars=2, a cross is blocked if TSI was on the
        'from' side for fewer than 2 consecutive bars before the cross."""
        analyzer = TsiAnalyzer(confirmation_bars=2)
        closes = []
        p = 100.0
        # 25-bar rally — TSI goes well above signal
        for _ in range(25):
            p += 1.0
            closes.append(p)
        # 2-bar dip — TSI may dip below signal briefly, but not for 2 bars
        for _ in range(2):
            p -= 2.0
            closes.append(p)
        # Rally bar — attempts a bullish cross back above
        p += 3.0
        closes.append(p)

        result = analyzer.analyze(closes)
        # Cross should be blocked because TSI was not below signal
        # for 2 consecutive bars before the cross attempt
        assert result.direction is None

    def test_confirmation_allows_sufficient_history(self):
        """With confirmation_bars=2, a cross is allowed if TSI was on the
        'from' side for at least 2 consecutive bars before the cross."""
        analyzer = TsiAnalyzer(confirmation_bars=2)
        closes = []
        p = 100.0
        # 20-bar rally — TSI goes above signal
        for _ in range(20):
            p += 1.0
            closes.append(p)
        # 5-bar decline — TSI goes below signal and stays there
        for _ in range(5):
            p -= 2.0
            closes.append(p)
        # 3 strong rally bars — bullish cross happens on the last one
        for _ in range(3):
            p += 5.0
            closes.append(p)

        result = analyzer.analyze(closes)
        assert result.direction == Direction.LONG
        assert result.tsi_value > result.signal_value

    def test_bearish_confirmation_blocks_insufficient_history(self):
        """With confirmation_bars=2, a bearish cross is blocked if TSI was
        not above signal for 2 consecutive bars before the cross."""
        analyzer = TsiAnalyzer(confirmation_bars=2)
        closes = []
        p = 100.0
        # 25-bar decline — TSI well below signal
        for _ in range(25):
            p -= 1.0
            closes.append(p)
        # 2-bar rally — TSI may briefly go above, but not for 2 bars
        for _ in range(2):
            p += 2.0
            closes.append(p)
        # Drop bar — attempts a bearish cross back below
        p -= 3.0
        closes.append(p)

        result = analyzer.analyze(closes)
        assert result.direction is None

    def test_bearish_confirmation_allows_sufficient_history(self):
        """With confirmation_bars=2, a bearish cross is allowed if TSI was
        above signal for at least 2 consecutive bars before the cross."""
        analyzer = TsiAnalyzer(confirmation_bars=2)
        closes = []
        p = 100.0
        # 20-bar decline — TSI below signal
        for _ in range(20):
            p -= 1.0
            closes.append(p)
        # 5-bar rally — TSI goes above signal and stays there
        for _ in range(5):
            p += 2.0
            closes.append(p)
        # 3 strong drop bars — bearish cross happens on the last one
        for _ in range(3):
            p -= 5.0
            closes.append(p)

        result = analyzer.analyze(closes)
        assert result.direction == Direction.SHORT
        assert result.tsi_value < result.signal_value
