"""Tests for fetcher provider helpers."""

from datetime import datetime, timezone

import pytest

from fetcher.utils import ensure_utc, symbol_to_pair


class TestEnsureUtc:
    def test_aware_datetime_preserved(self):
        dt = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        assert ensure_utc(dt) is dt

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2024, 1, 1, 12, 0)
        result = ensure_utc(dt)
        assert result.tzinfo is timezone.utc


class TestSymbolToPair:
    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("NQ=F", "NQ"),
            ("NQ:XCME", "NQ"),
            ("NQ1!", "NQ"),
            ("NQ.FUT", "NQ"),
            ("NQH25:XCME", "NQH25"),
            ("AAPL", "AAPL"),
            ("ES=F", "ES"),
        ],
    )
    def test_mappings(self, symbol: str, expected: str):
        assert symbol_to_pair(symbol) == expected
