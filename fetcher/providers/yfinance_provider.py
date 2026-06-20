"""
Yahoo Finance provider (via yfinance).

Symbol for NQ E-mini Futures continuous contract: "NQ=F"

LIMITATIONS:
  - 1m bars: maximum 7 calendar days of history
  - Data quality: Yahoo Finance aggregates exchange data; may have small gaps
    during off-hours or around contract roll dates
  - No API key required — completely free

USAGE:
    provider = YFinanceProvider()
    bars = provider.fetch_bars("NQ=F", start=..., end=...)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from ..base import FetchProvider
from ..types import Bar
from ..utils import ensure_utc, symbol_to_pair

logger = logging.getLogger(__name__)

_MAX_HISTORY_DAYS = 7   # yfinance hard limit for 1m interval
_UTC = timezone.utc


class YFinanceProvider(FetchProvider):
    """
    Market data from Yahoo Finance — free, no API key, limited history.

    When the CSV is empty and lookback > 7 days, DataSyncer will automatically
    clamp the request to 7 days and log a warning.
    """

    @property
    def name(self) -> str:
        return "Yahoo Finance (yfinance)"

    @property
    def max_history_days(self) -> int:
        return _MAX_HISTORY_DAYS

    def fetch_bars(self, symbol: str, start: datetime, end: datetime) -> List[Bar]:
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError(
                "yfinance is not installed. Run: poetry add yfinance"
            )

        start, end = ensure_utc(start), ensure_utc(end)

        # Hard clamp: yfinance won't return 1m data older than ~7 days
        earliest = datetime.now(_UTC) - timedelta(days=_MAX_HISTORY_DAYS - 1)
        if start < earliest:
            logger.warning(
                f"yfinance 1m data is only available for the last {_MAX_HISTORY_DAYS} days. "
                f"Clamping start from {start.date()} to {earliest.date()}."
            )
            start = earliest

        if start >= end:
            logger.info("Adjusted start is at or after end — nothing to fetch.")
            return []

        logger.info(f"[yfinance] Fetching {symbol}: {start.isoformat()} → {end.isoformat()}")

        ticker = yf.Ticker(symbol)
        df = ticker.history(
            interval="1m",
            start=start,
            end=end,
            auto_adjust=True,
            prepost=True,   # include pre/post-market (futures trade nearly 24h)
        )

        if df.empty:
            logger.warning(f"[yfinance] No data returned for {symbol}.")
            return []

        # Normalize index to UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        pair = symbol_to_pair(symbol)
        bars: List[Bar] = []

        for ts, row in df.iterrows():
            bars.append({
                "time":   int(ts.timestamp()),
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": int(row.get("Volume", 0)),
                "pair":   pair,
            })

        bars.sort(key=lambda b: b["time"])
        logger.info(f"[yfinance] Returned {len(bars)} bars for {symbol}.")
        return bars



