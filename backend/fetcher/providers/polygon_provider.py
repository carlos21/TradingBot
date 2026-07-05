"""
Polygon.io provider for futures market data.

REQUIREMENTS:
  - Polygon.io account with a plan that includes futures data (Starter or above)
  - API key: set POLYGON_API_KEY environment variable, or pass api_key= directly

NQ FUTURES SYMBOL on Polygon:
  - Continuous (front-month): "NQ:XCME"  ← recommended for daily syncing
  - Specific contract (e.g. March 2025):  "NQH25:XCME"

DOCS: https://polygon.io/docs/futures/get_v2_aggs_ticker__futuresTicker__range__multiplier__timespan__from__to

USAGE:
    provider = PolygonProvider()            # reads POLYGON_API_KEY from env
    provider = PolygonProvider("your_key")  # explicit key
    bars = provider.fetch_bars("NQ:XCME", start=..., end=...)
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from ..base import FetchProvider
from ..types import Bar
from ..utils import ensure_utc, symbol_to_pair

logger = logging.getLogger(__name__)

_UTC      = timezone.utc
_BASE_URL = "https://api.polygon.io"


class PolygonProvider(FetchProvider):
    """
    Polygon.io market data — full history, requires API key.

    Handles automatic pagination (Polygon pages results in chunks of up to
    50,000 bars) and rate-limit back-off.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Polygon.io API key.
                     Falls back to the POLYGON_API_KEY environment variable.
        """
        self.api_key = api_key or os.environ.get("POLYGON_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Polygon.io API key is required. "
                "Set the POLYGON_API_KEY environment variable "
                "or pass api_key= to PolygonProvider()."
            )

    @property
    def name(self) -> str:
        return "Polygon.io"

    @property
    def max_history_days(self) -> int:
        return -1  # Polygon supports years of history

    def fetch_bars(self, symbol: str, start: datetime, end: datetime) -> List[Bar]:
        """
        Fetch 1-minute aggregated bars from Polygon.io.

        Args:
            symbol: Polygon futures ticker (e.g., "NQ:XCME").
        """
        start, end = ensure_utc(start), ensure_utc(end)

        # Polygon uses millisecond epoch timestamps
        from_ms = int(start.timestamp() * 1000)
        to_ms   = int(end.timestamp() * 1000)

        url = (
            f"{_BASE_URL}/v2/aggs/ticker/{symbol}"
            f"/range/1/minute/{from_ms}/{to_ms}"
        )
        params = {
            "adjusted": "true",
            "sort":     "asc",
            "limit":    50000,
            "apiKey":   self.api_key,
        }

        pair = symbol_to_pair(symbol)
        bars: List[Bar] = []

        logger.info(
            f"[Polygon] Fetching {symbol}: "
            f"{start.isoformat()} → {end.isoformat()}"
        )

        while url:
            resp = self._get(url, params)
            data = resp.json()

            status = data.get("status", "")
            if status not in ("OK", "DELAYED"):
                logger.warning(f"[Polygon] Unexpected status '{status}': {data}")

            for r in data.get("results", []):
                bars.append({
                    "time":   r["t"] // 1000,   # ms → seconds
                    "open":   float(r["o"]),
                    "high":   float(r["h"]),
                    "low":    float(r["l"]),
                    "close":  float(r["c"]),
                    "volume": int(r.get("v", 0)),
                    "pair":   pair,
                })

            # Pagination: follow next_url if present
            url = data.get("next_url")
            params = {"apiKey": self.api_key}  # next_url already carries other params
            if url:
                time.sleep(0.1)  # gentle rate limiting between pages

        logger.info(f"[Polygon] Returned {len(bars)} bars for {symbol}.")
        return bars

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict, retries: int = 3) -> requests.Response:
        for attempt in range(retries):
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                logger.warning(
                    f"[Polygon] Rate limited (429). Waiting {wait}s before retry..."
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()  # re-raise after exhausting retries



