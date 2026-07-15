"""Tests for src.strategies.indicators.tsi."""

import pytest

from src.strategies.indicators.tsi import calculate_ema, calculate_tsi_series


class TestCalculateEma:

    def test_basic(self):
        values = [100.0, 101.0, 102.0, 101.0, 100.0]
        ema = calculate_ema(values, 3)
        assert len(ema) == len(values)
        assert ema[0] == 100.0

    def test_empty(self):
        assert calculate_ema([], 3) == []

    def test_invalid_length_zero(self):
        with pytest.raises(ValueError, match="EMA length must be positive"):
            calculate_ema([100.0], 0)

    def test_invalid_length_negative(self):
        with pytest.raises(ValueError, match="EMA length must be positive"):
            calculate_ema([100.0], -1)

    def test_single_value(self):
        assert calculate_ema([50.0], 5) == [50.0]

    def test_constant_values(self):
        values = [100.0] * 10
        assert calculate_ema(values, 5) == values

    def test_ascending_values(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        ema = calculate_ema(values, 3)
        assert ema[0] == 1.0
        assert ema[-1] > ema[-2]


class TestCalculateTsiSeries:

    def test_constant_prices(self):
        closes = [100.0] * 30
        tsi, sig = calculate_tsi_series(closes, 6, 13, 4)
        assert all(v == 0.0 for v in tsi)
        assert all(v == 0.0 for v in sig)

    def test_too_few_bars(self):
        closes = [100.0] * 10
        tsi, sig = calculate_tsi_series(closes, 6, 13, 4)
        assert tsi == []
        assert sig == []

    def test_invalid_lengths(self):
        with pytest.raises(ValueError, match="TSI lengths must be positive"):
            calculate_tsi_series([100.0] * 30, 0, 13, 4)
        with pytest.raises(ValueError, match="TSI lengths must be positive"):
            calculate_tsi_series([100.0] * 30, 6, 0, 4)
        with pytest.raises(ValueError, match="TSI lengths must be positive"):
            calculate_tsi_series([100.0] * 30, 6, 13, 0)

    def test_minimum_required_bars(self):
        closes = [100.0] * (6 + 13 + 4)
        tsi, sig = calculate_tsi_series(closes, 6, 13, 4)
        assert len(tsi) == len(closes)
        assert len(sig) == len(closes)

    def test_bullish_trend_positive_tsi(self):
        closes = [100.0 + i * 2 for i in range(50)]
        tsi, sig = calculate_tsi_series(closes, 6, 13, 4)
        assert tsi
        assert sig
        # In a strong sustained uptrend TSI should end positive.
        assert tsi[-1] > 0

    def test_bearish_trend_negative_tsi(self):
        closes = [200.0 - i * 2 for i in range(50)]
        tsi, sig = calculate_tsi_series(closes, 6, 13, 4)
        assert tsi
        assert sig
        assert tsi[-1] < 0

    def test_series_lengths_match_input(self):
        closes = [100.0 + (i % 5) for i in range(60)]
        tsi, sig = calculate_tsi_series(closes, 6, 13, 4)
        assert len(tsi) == len(closes)
        assert len(sig) == len(closes)

    def test_zero_abs_price_change_handled(self):
        # First element has zero price change by construction; the rest flat.
        closes = [100.0] * 40
        tsi, sig = calculate_tsi_series(closes, 6, 13, 4)
        assert all(v == 0.0 for v in tsi)
