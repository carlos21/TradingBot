"""Virtual time resolution for backtest/replay scenarios.

When running in replay mode, 'now' is not the system clock — it's the
last timestamp from the replayed data. This service encapsulates that logic.
"""

from datetime import datetime, timezone

from src.bars_loader import BarsLoader


class VirtualTimeResolver:
    """Resolve the current 'virtual' timestamp for trading operations.

    Priority:
    1. Loader's last played tick (during replay).
    2. Last bar in data source history (if paused/stopped).
    3. System time (fallback).
    """

    def __init__(self, bars_loader: BarsLoader):
        self._bars_loader = bars_loader

    def now(self) -> float:
        """Return the current virtual time as a Unix timestamp."""
        if self._bars_loader._last_played_ts > 0:
            return self._bars_loader._last_played_ts

        bars = self._bars_loader.data_source.load_historical_bars('1m')
        if bars:
            return bars[-1]['time']

        return datetime.now(timezone.utc).timestamp()
