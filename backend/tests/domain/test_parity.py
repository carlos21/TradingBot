"""Tests for src.domain.parity."""
from typing import Any

import pytest

from src.domain.parity import GapInfo, IMarketClosureFilter, IParityChecker, ParityResult


class TestGapInfo:
    def test_creation(self):
        gap = GapInfo(
            start_time=100,
            end_time=200,
            duration_seconds=100,
            gap_type="missing",
            details="missing bar",
            is_market_closed=False,
        )
        assert gap.start_time == 100
        assert gap.is_market_closed is False


class TestParityResult:
    def test_to_dict(self):
        gap = GapInfo(
            start_time=100,
            end_time=200,
            duration_seconds=100,
            gap_type="mismatch",
            details="ohlc differ",
            is_market_closed=True,
        )
        result = ParityResult(
            checked_at=50,
            bars_checked=10,
            gaps_found=1,
            gaps=[gap],
            all_good=False,
            summary="1 gap found",
        )
        assert result.to_dict() == {
            "checked_at": 50,
            "bars_checked": 10,
            "gaps_found": 1,
            "gaps": [
                {
                    "start_time": 100,
                    "end_time": 200,
                    "duration_seconds": 100,
                    "gap_type": "mismatch",
                    "details": "ohlc differ",
                    "is_market_closed": True,
                }
            ],
            "all_good": False,
            "summary": "1 gap found",
        }

    def test_to_dict_empty_gaps(self):
        result = ParityResult(
            checked_at=1,
            bars_checked=5,
            gaps_found=0,
            gaps=[],
            all_good=True,
            summary="all good",
        )
        assert result.to_dict() == {
            "checked_at": 1,
            "bars_checked": 5,
            "gaps_found": 0,
            "gaps": [],
            "all_good": True,
            "summary": "all good",
        }


class FakeParityChecker(IParityChecker):
    def check(self, local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> ParityResult:
        return ParityResult(
            checked_at=0,
            bars_checked=len(local) + len(remote),
            gaps_found=0,
            gaps=[],
            all_good=True,
            summary="ok",
        )


class FakeMarketClosureFilter(IMarketClosureFilter):
    def is_market_closed_gap(self, start_time: int, end_time: int) -> bool:
        return start_time > 1000


class TestProtocols:
    def test_parity_checker_protocol(self):
        checker = FakeParityChecker()
        result = checker.check([], [])
        assert result.all_good is True

    def test_market_closure_filter_protocol(self):
        filter_ = FakeMarketClosureFilter()
        assert filter_.is_market_closed_gap(2000, 3000) is True
        assert filter_.is_market_closed_gap(100, 200) is False
