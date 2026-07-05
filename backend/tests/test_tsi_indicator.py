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
