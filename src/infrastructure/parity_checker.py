"""Concrete parity checker implementation.

Wraps BarComparer and converts AuditResult into a ParityResult with
detailed gap information and market-closure filtering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domain.parity import GapInfo, IMarketClosureFilter, IParityChecker, ParityResult
from src.infrastructure.bar_auditor import AuditResult, BarComparer, BarMismatch


class NinjaTraderParityChecker:
    """Compare local Python bars against remote NinjaTrader bars."""

    def __init__(
        self,
        market_filter: IMarketClosureFilter,
        comparer: BarComparer | None = None,
    ):
        self._market_filter = market_filter
        self._comparer = comparer or BarComparer()

    def check(self, local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> ParityResult:
        """Run comparison and build ParityResult with gap details."""
        import time

        audit = self._comparer.compare(local, remote)
        checked_at = int(time.time())
        bars_checked = max(len(local), len(remote))

        gaps: list[GapInfo] = []
        for mismatch in audit.details:
            gap = self._make_gap(mismatch)
            gaps.append(gap)

        # Count only non-market-closed gaps for the alert
        actionable_gaps = [g for g in gaps if not g.is_market_closed]
        gaps_found = len(actionable_gaps)
        all_good = gaps_found == 0

        if all_good:
            if len(gaps) > 0:
                summary = (
                    f"All {bars_checked} bars match — "
                    f"{len(gaps)} market-closed gap(s) excluded"
                )
            else:
                summary = f"All {bars_checked} bars match perfectly"
        else:
            summary = (
                f"{gaps_found} actionable gap(s) found in {bars_checked} bars "
                f"({len(gaps) - gaps_found} market-closed excluded)"
            )

        return ParityResult(
            checked_at=checked_at,
            bars_checked=bars_checked,
            gaps_found=gaps_found,
            gaps=gaps,
            all_good=all_good,
            summary=summary,
        )

    def _make_gap(self, mismatch: BarMismatch) -> GapInfo:
        """Convert a BarMismatch into a GapInfo with market-closure check."""
        if mismatch.local_bar is None:
            gap_type = "missing"
            details = self._fmt_bar(mismatch.remote_bar)
            start_time = mismatch.time
            end_time = mismatch.time + 60  # assume 1m bar
        elif mismatch.remote_bar is None:
            gap_type = "extra"
            details = self._fmt_bar(mismatch.local_bar)
            start_time = mismatch.time
            end_time = mismatch.time + 60
        else:
            gap_type = "mismatch"
            details = self._fmt_diff(mismatch.field_differences)
            start_time = mismatch.time
            end_time = mismatch.time + 60

        duration = end_time - start_time
        is_closed = self._market_filter.is_market_closed_gap(start_time, end_time)

        return GapInfo(
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration,
            gap_type=gap_type,  # type: ignore[arg-type]
            details=details,
            is_market_closed=is_closed,
        )

    @staticmethod
    def _fmt_bar(bar: dict[str, Any] | None) -> str:
        if bar is None:
            return "None"
        return (
            f"O={bar.get('open')} H={bar.get('high')} "
            f"L={bar.get('low')} C={bar.get('close')} V={bar.get('volume', 0)}"
        )

    @staticmethod
    def _fmt_diff(diffs: dict[str, tuple[Any, Any]]) -> str:
        parts = []
        for field, (lv, rv) in diffs.items():
            parts.append(f"{field}: {lv} vs {rv}")
        return ", ".join(parts)
