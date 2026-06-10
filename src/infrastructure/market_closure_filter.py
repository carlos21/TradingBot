"""Market closure filter for CME futures (MNQ).

Determines whether a gap between two bar timestamps falls during a
market-closed period (daily maintenance or weekend).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
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
        if self._is_maintenance_gap(dt_start, dt_end):
            return True

        return False

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
        if day_start == 4 and dt_start.hour >= 16:
            return True

        return False

    def _is_maintenance_gap(self, dt_start: datetime, dt_end: datetime) -> bool:
        """Return True if gap is the daily 16:00–17:01 CDT maintenance window."""
        # Only check same-day gaps
        if dt_start.date() != dt_end.date():
            return False

        # Maintenance window: 16:00:00 – 17:01:00 CDT
        start_sec = dt_start.hour * 3600 + dt_start.minute * 60 + dt_start.second
        end_sec = dt_end.hour * 3600 + dt_end.minute * 60 + dt_end.second

        # If gap starts at or after 16:00 and ends at or before 17:01
        if start_sec >= 16 * 3600 and end_sec <= 17 * 3600 + 60:
            return True

        # Also catch the classic 3660s gap (61 minutes)
        gap_seconds = int((dt_end - dt_start).total_seconds())
        if gap_seconds == 3660 and start_sec >= 16 * 3600:
            return True

        return False
