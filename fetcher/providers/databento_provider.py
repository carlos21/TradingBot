"""
Databento provider for CME MNQ futures market data.

REQUIREMENTS:
  - Databento account at databento.com (free $10 credit on signup)
  - API key: set DATABENTO_API_KEY environment variable, or pass api_key= directly
  - Install: poetry add databento

NQ FUTURES SYMBOL on Databento:
  - Continuous front-month: "NQ.FUT" with stype_in="parent"  ← recommended
  - Specific contract (e.g. March 2025): "NQH5" with stype_in="raw_symbol"

DATASET: GLBX.MDP3  (CME Globex)

DOCS: https://databento.com/docs

USAGE:
    provider = DatabentoProvider()            # reads DATABENTO_API_KEY from env
    provider = DatabentoProvider("your_key")  # explicit key
    bars = provider.fetch_bars("NQ.FUT", start=..., end=...)
"""

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from ..base import FetchProvider
from ..types import Bar

logger = logging.getLogger(__name__)

_UTC     = timezone.utc
_DATASET = "GLBX.MDP3"
_SCHEMA  = "ohlcv-1m"


class DatabentoProvider(FetchProvider):
    """
    Databento CME futures data — full history, requires API key.

    Uses the databento Python client to fetch 1-minute OHLCV bars
    from CME Globex (GLBX.MDP3).
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("DATABENTO_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Databento API key is required. "
                "Set the DATABENTO_API_KEY environment variable "
                "or pass api_key= to DatabentoProvider()."
            )

    @property
    def name(self) -> str:
        return "Databento (CME GLBX.MDP3)"

    @property
    def max_history_days(self) -> int:
        return -1  # Databento supports full CME history

    def fetch_bars(self, symbol: str, start: datetime, end: datetime) -> List[Bar]:
        """
        Fetch 1-minute OHLCV bars from Databento.

        Args:
            symbol: Databento symbol. Use "NQ.FUT" for the continuous
                    front-month contract (recommended), or a specific
                    contract like "NQH5".
        """
        try:
            import databento as db
        except ImportError:
            raise ImportError(
                "databento is not installed. Run: poetry add databento"
            )

        start, end = _ensure_utc(start), _ensure_utc(end)

        # Determine stype based on symbol format
        if symbol.endswith(".c.0") or symbol.endswith(".c.1"):
            stype = "continuous"
        elif symbol.endswith(".FUT"):
            stype = "parent"
        else:
            stype = "raw_symbol"

        logger.info(
            f"[Databento] Fetching {symbol} ({_DATASET}/{_SCHEMA}): "
            f"{start.isoformat()} → {end.isoformat()}"
        )

        client = db.Historical(self.api_key)

        # Clamp end to the dataset's available range (Databento lags a few minutes behind live)
        dataset_range = client.metadata.get_dataset_range(dataset=_DATASET)
        available_end = _parse_databento_ts(dataset_range["schema"][_SCHEMA]["end"])
        if end > available_end:
            logger.info(
                f"[Databento] Clamping end from {end.isoformat()} "
                f"to available {available_end.isoformat()}"
            )
            end = available_end

        if start >= end:
            logger.info("[Databento] Nothing to fetch after clamping end.")
            return []

        data = client.timeseries.get_range(
            dataset=_DATASET,
            symbols=symbol,
            stype_in=stype,
            schema=_SCHEMA,
            start=start.strftime("%Y-%m-%dT%H:%M:%S"),
            end=end.strftime("%Y-%m-%dT%H:%M:%S"),
        )

        df = data.to_df()

        if df.empty:
            logger.warning(f"[Databento] No data returned for {symbol}.")
            return []

        # Normalize index to UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        pair = _symbol_to_pair(symbol)
        bars: List[Bar] = [
            {
                "time":   int(ts.timestamp()),
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": int(row.get("volume", 0)),
                "pair":   pair,
            }
            for ts, row in df.iterrows()
        ]

        bars.sort(key=lambda b: b["time"])
        logger.info(f"[Databento] Returned {len(bars)} bars for {symbol}.")
        return bars


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=_UTC)


def _parse_databento_ts(ts: str) -> datetime:
    """Parse Databento ISO timestamp (may have nanosecond precision) to UTC datetime."""
    # Trim to microsecond precision that Python can handle: '2026-03-07T22:27:01.102310000Z'
    ts = ts.rstrip("Z")
    if "." in ts:
        base, frac = ts.split(".", 1)
        ts = f"{base}.{frac[:6]}"  # truncate to microseconds
    return datetime.fromisoformat(ts).replace(tzinfo=_UTC)


def _symbol_to_pair(symbol: str) -> str:
    """'NQ.FUT' → 'MNQ',  'NQH5' → 'MNQ'"""
    return symbol.split(".")[0].rstrip("FGHMNQUVXZ0123456789").upper() or symbol.split(".")[0].upper()
