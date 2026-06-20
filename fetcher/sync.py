"""
DataSyncer: orchestrates gap detection, fetching, and CSV merging.

Logic:
  - CSV is empty  → fetch DEFAULT_LOOKBACK_DAYS days of history (or provider max)
  - CSV has data  → fetch from (last bar + 1 minute) up to now
  - Provider has a max_history_days limit → warn and clamp automatically
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .base import FetchProvider
from .csv_store import CSVStore

logger = logging.getLogger(__name__)

_UTC = timezone.utc
DEFAULT_LOOKBACK_DAYS = 14


class DataSyncer:
    """
    Keeps a CSVStore up to date by fetching missing bars from a FetchProvider.

    Typical daily usage:
        syncer = DataSyncer(provider, store)
        syncer.sync(symbol="NQ=F")
    """

    def __init__(self, provider: FetchProvider, store: CSVStore):
        self.provider = provider
        self.store    = store

    def sync(
        self,
        symbol: str,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        end: Optional[datetime] = None,
    ) -> int:
        """
        Fetch and merge any missing bars into the CSV store.

        Args:
            symbol:        Provider-specific symbol (e.g., "NQ=F").
            lookback_days: Days to fetch when the CSV is empty. Clamped to
                           the provider's max_history_days automatically.
            end:           Fetch up to this UTC datetime. Defaults to now.

        Returns:
            Number of new bars written to the CSV.
        """
        if lookback_days < 0:
            raise ValueError(f"lookback_days must be non-negative, got {lookback_days}")

        now = end or datetime.now(_UTC)
        last_ts = self.store.get_last_timestamp()

        start = self._compute_start(last_ts, now, lookback_days)
        if start is None:
            return 0

        try:
            bars = self.provider.fetch_bars(symbol, start, now)
        except Exception as exc:
            logger.error(f"Provider '{self.provider.name}' fetch failed: {exc}")
            raise

        if not bars:
            logger.info("Provider returned 0 bars for the requested range.")
            return 0

        logger.info(f"Fetched {len(bars)} bars from {self.provider.name}.")
        written = self.store.write_new_bars(bars)
        logger.info(f"Sync complete. {written} new bars written.")
        return written

    # ── Internal ─────────────────────────────────────────────────────────────

    def _compute_start(
        self,
        last_ts: Optional[int],
        now: datetime,
        lookback_days: int,
    ) -> Optional[datetime]:
        if last_ts is None:
            # Empty CSV: use full lookback, clamped to provider limit
            days = self._clamp_to_provider(lookback_days)
            start = now - timedelta(days=days)
            logger.info(
                f"CSV is empty. Fetching {days} days of history "
                f"({start.date()} → {now.date()})."
            )
            return start

        # Has data: fetch from just after the last bar
        start = datetime.fromtimestamp(last_ts, tz=_UTC) + timedelta(minutes=1)
        if start >= now:
            logger.info(
                f"CSV is already up to date "
                f"(last bar: {datetime.fromtimestamp(last_ts, tz=_UTC).isoformat()})."
            )
            return None

        logger.info(
            f"CSV last bar: {datetime.fromtimestamp(last_ts, tz=_UTC).isoformat()}. "
            f"Fetching from {start.isoformat()} → {now.isoformat()}."
        )
        return start

    def _clamp_to_provider(self, requested_days: int) -> int:
        limit = self.provider.max_history_days
        if limit == -1 or requested_days <= limit:
            return requested_days
        logger.warning(
            f"Provider '{self.provider.name}' supports at most {limit} days of "
            f"1m history. Clamping requested {requested_days} → {limit} days."
        )
        return limit
