"""Unit tests for src.strategies.liquidity_v2.controllers.validators."""

import pytest

from src.strategies.liquidity_v2.controllers.validators import LineInputValidator


class TestValidatePrice:
    def test_accepts_positive_float(self):
        assert LineInputValidator.validate_price(100.0) == 100.0

    def test_accepts_positive_int(self):
        assert LineInputValidator.validate_price(100) == 100.0

    def test_accepts_positive_numeric_string(self):
        assert LineInputValidator.validate_price("100.5") == 100.5

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="price must be a numeric value"):
            LineInputValidator.validate_price("abc")

    def test_rejects_none(self):
        with pytest.raises(ValueError, match="price must be a numeric value"):
            LineInputValidator.validate_price(None)

    def test_rejects_zero(self):
        with pytest.raises(ValueError, match="price must be positive"):
            LineInputValidator.validate_price(0)

    def test_rejects_negative(self):
        with pytest.raises(ValueError, match="price must be positive"):
            LineInputValidator.validate_price(-10.0)


class TestValidateCreationTimestamp:
    def test_accepts_float(self):
        assert LineInputValidator.validate_creation_timestamp(1700000000.0) == 1700000000.0

    def test_accepts_int(self):
        assert LineInputValidator.validate_creation_timestamp(1700000000) == 1700000000.0

    def test_accepts_numeric_string(self):
        assert LineInputValidator.validate_creation_timestamp("1700000000") == 1700000000.0

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="creation_timestamp must be a numeric timestamp"):
            LineInputValidator.validate_creation_timestamp("bad")

    def test_rejects_none(self):
        with pytest.raises(ValueError, match="creation_timestamp must be a numeric timestamp"):
            LineInputValidator.validate_creation_timestamp(None)
