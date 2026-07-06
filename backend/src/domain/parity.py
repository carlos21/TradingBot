"""Domain models for bar parity checking.

Pure domain — no infrastructure imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class GapInfo:
    """A single discrepancy between local and remote bars."""

    start_time: int
    end_time: int
    duration_seconds: int
    gap_type: Literal["missing", "extra", "mismatch"]
    details: str
    is_market_closed: bool


@dataclass(frozen=True)
class ParityResult:
    """Result of a parity check between two bar sources."""

    checked_at: int
    bars_checked: int
    gaps_found: int
    gaps: list[GapInfo]
    all_good: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return {
            "checked_at": self.checked_at,
            "bars_checked": self.bars_checked,
            "gaps_found": self.gaps_found,
            "gaps": [
                {
                    "start_time": g.start_time,
                    "end_time": g.end_time,
                    "duration_seconds": g.duration_seconds,
                    "gap_type": g.gap_type,
                    "details": g.details,
                    "is_market_closed": g.is_market_closed,
                }
                for g in self.gaps
            ],
            "all_good": self.all_good,
            "summary": self.summary,
        }


class IParityChecker(Protocol):
    """Protocol for comparing two bar sequences and producing a ParityResult."""

    def check(self, local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> ParityResult:
        ...


class IMarketClosureFilter(Protocol):
    """Protocol for determining whether a gap falls during market closure."""

    def is_market_closed_gap(self, start_time: int, end_time: int) -> bool:
        ...
