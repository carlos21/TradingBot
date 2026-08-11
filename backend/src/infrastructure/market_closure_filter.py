"""Market closure filter for CME futures (MNQ).

Determines whether a gap between two bar timestamps falls during a
market-closed period (daily maintenance or weekend).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


class MarketClosureFilter:
    """Filter gaps that occur during market closure periods.

    CME Equity Index Futures (MNQ, NQ, ES, etc.):
      - Daily maintenance: 16:00 – 17:00 CDT (Mon–Thu)
      - Friday close: 16:00 CDT → Sunday open: 17:00 CDT
      - Saturday is always closed
    """

    def __init__(
        self,
        instrument: str = "MNQ",
        tz: str = "America/Chicago",
    ):
        self._instrument = instrument
        self._tz = ZoneInfo(tz)

    def is_market_closed_gap(self, start_time: int, end_time: int) -> bool:
        """Return True if the gap falls entirely within a market-closed period."""
        dt_start = datetime.fromtimestamp(start_time, tz=self._tz)
        dt_end = datetime.fromtimestamp(end_time, tz=self._tz)

        # Weekend check
        if self._is_weekend_gap(dt_start, dt_end):
            return True

        # Daily maintenance window check (16:00 – 17:01 CDT)
        return bool(self._is_maintenance_gap(dt_start, dt_end))

    def _is_weekend_gap(self, dt_start: datetime, dt_end: datetime) -> bool:
        """Return True if gap spans Saturday or Sunday maintenance."""
        # CME closes Friday 16:00 CDT, reopens Sunday 17:00 CDT
        # Any gap that touches Saturday is considered weekend-closed
        day_start = dt_start.weekday()
        day_end = dt_end.weekday()

        # Saturday = 5, Sunday = 6
        if day_start == 5 or day_end == 5:
            return True
        if day_start == 6 and dt_start.hour < 17:
            return True
        if day_end == 6 and dt_end.hour < 17:
            return True

        # Friday after 16:00 through Sunday before 17:00
        return bool(day_start == 4 and dt_start.hour >= 16)

    def _is_maintenance_gap(self, dt_start: datetime, dt_end: datetime) -> bool:
        """Return True if gap is the daily 16:00–17:00 CDT maintenance window.

        Real bar streams often place the last pre-close bar a minute or two
        before 16:00 and the first post-reopen bar a minute or two after 17:00,
        so we detect the gap by its overlap with the maintenance window rather
        than requiring exact boundaries.
        """
        # Only check same-day gaps
        if dt_start.date() != dt_end.date():
            return False

        start_sec = dt_start.hour * 3600 + dt_start.minute * 60 + dt_start.second
        end_sec = dt_end.hour * 3600 + dt_end.minute * 60 + dt_end.second
        gap_seconds = int((dt_end - dt_start).total_seconds())

        maint_start = 16 * 3600
        maint_end = 17 * 3600
        # Include the 17:00 bar itself (covers 17:00:00-17:00:59)
        maint_end_extended = maint_end + 60

        # Case 1: the entire gap falls inside the maintenance window
        # (e.g. a single 1m bar during the 16:00-17:00 CDT halt)
        if start_sec >= maint_start and end_sec <= maint_end_extended:
            return True

        # Case 2: gap covers a large portion of the maintenance window
        overlap_start = max(start_sec, maint_start)
        overlap_end = min(end_sec, maint_end)
        overlap = max(0, overlap_end - overlap_start)
        if overlap >= 50 * 60:  # at least 50 min overlap
            return True

        # Case 3: classic maintenance-shaped gap starting near 16:00 and ending
        # near 17:00, allowing a few minutes of slack on each side.
        if gap_seconds >= 55 * 60 and start_sec >= maint_start - 5 * 60 and end_sec <= maint_end + 5 * 60:
            return True

        return False
