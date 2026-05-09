"""Price resolution for trade entry/exit operations.

Encapsulates the logic of finding the current market price from various
loader and data source states.
"""

from src.bars_loader import BarsLoader


class PriceResolver:
    """Resolve the current price for opening or closing trades."""

    def __init__(self, bars_loader: BarsLoader):
        self._bars_loader = bars_loader

    def current_price(self) -> float | None:
        """Return the most recent close price, or None if no data available."""
        if self._bars_loader._1m_buffer:
            return self._bars_loader._1m_buffer[-1]['close']

        bars = self._bars_loader.data_source.load_historical_bars('1m')
        if bars:
            return bars[-1]['close']

        if self._bars_loader._last_bar_close > 0:
            return self._bars_loader._last_bar_close

        return None
