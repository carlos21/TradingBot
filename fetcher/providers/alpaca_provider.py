"""
Alpaca Markets provider for futures market data.

REQUIREMENTS:
  - Free Alpaca account at alpaca.markets (no credit card needed)
  - API key + secret: set ALPACA_API_KEY and ALPACA_SECRET_KEY env vars,
    or pass them directly to AlpacaProvider()

NQ FUTURES SYMBOL on Alpaca:
  - Continuous front-month: "NQ1!"  ← recommended

DOCS: https://docs.alpaca.markets/reference/getv1beta1futuresbars

USAGE:
    provider = AlpacaProvider()                        # reads env vars
    provider = AlpacaProvider("key", "secret")         # explicit
    bars = provider.fetch_bars("NQ1!", start=..., end=...)
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from ..base import FetchProvider
from ..types import Bar

logger = logging.getLogger(__name__)

_UTC      = timezone.utc
_BASE_URL = "https://data.alpaca.markets"


class AlpacaProvider(FetchProvider):
    """
    Alpaca Markets futures data — free account, years of 1m history.

    Handles automatic pagination (next_page_token) and rate-limit back-off.
    """

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        self.api_key    = api_key    or os.environ.get("ALPACA_API_KEY")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Alpaca API key and secret are required. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables "
                "or pass them to AlpacaProvider()."
            )

    @property
    def name(self) -> str:
        return "Alpaca Markets"

    @property
    def max_history_days(self) -> int:
        return -1  # Alpaca supports years of 1m history

    def fetch_bars(self, symbol: str, start: datetime, end: datetime) -> List[Bar]:
        """
        Fetch 1-minute aggregated bars from Alpaca futures API.

        Args:
            symbol: Alpaca futures ticker (e.g., "NQ1!").
        """
        start, end = _ensure_utc(start), _ensure_utc(end)

        url = f"{_BASE_URL}/v1beta1/futures/bars"
        params = {
            "symbols":   symbol,
            "timeframe": "1Min",
            "start":     start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":       end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit":     10000,
            "sort":      "asc",
        }
        headers = {
            "APCA-API-KEY-ID":     self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

        pair = _symbol_to_pair(symbol)
        bars: List[Bar] = []

        logger.info(
            f"[Alpaca] Fetching {symbol}: "
            f"{start.isoformat()} → {end.isoformat()}"
        )

        while True:
            resp = self._get(url, params, headers)
            data = resp.json()

            for bar in data.get("bars", {}).get(symbol, []):
                ts = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
                bars.append({
                    "time":   int(ts.timestamp()),
                    "open":   float(bar["o"]),
                    "high":   float(bar["h"]),
                    "low":    float(bar["l"]),
                    "close":  float(bar["c"]),
                    "volume": int(bar.get("v", 0)),
                    "pair":   pair,
                })

            next_token = data.get("next_page_token")
            if not next_token:
                break

            params = {
                "symbols":        symbol,
                "timeframe":      "1Min",
                "limit":          10000,
                "sort":           "asc",
                "page_token":     next_token,
            }
            time.sleep(0.1)  # gentle rate limiting between pages

        logger.info(f"[Alpaca] Returned {len(bars)} bars for {symbol}.")
        return bars

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict, headers: dict, retries: int = 3) -> requests.Response:
        for attempt in range(retries):
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                logger.warning(
                    f"[Alpaca] Rate limited (429). Waiting {wait}s before retry..."
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=_UTC)


def _symbol_to_pair(symbol: str) -> str:
    """'NQ1!' → 'MNQ',  'ES1!' → 'ES'"""
    return symbol.rstrip("1!").upper()
