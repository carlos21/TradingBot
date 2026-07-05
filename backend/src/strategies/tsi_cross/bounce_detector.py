"""Bounce detection for line-less TSI strategy.

A "bounce" is a local price extremum (swing low for longs, swing high for
shorts) used as the stop-loss reference.  The detector is pluggable so the
user can swap algorithms without touching the strategy.
"""

from typing import Protocol


class BounceDetector(Protocol):
    """Protocol for bounce detection algorithms."""

    def find_previous_low_bounce(self, bars: list[dict]) -> float:
        """Return the most recent significant low (used as SL for longs)."""
        ...

    def find_previous_high_bounce(self, bars: list[dict]) -> float:
        """Return the most recent significant high (used as SL for shorts)."""
        ...


class SwingBounceDetector:
    """Detects swing lows/highs by looking for local extrema.

    A local minimum is confirmed when a bar's low is lower than the lows of
    ``neighbor_bars`` bars on each side.  If no proper swing is found within
    ``lookback``, falls back to the absolute min/max.
    """

    def __init__(self, lookback: int = 20, neighbor_bars: int = 1):
        self.lookback = lookback
        self.neighbor_bars = neighbor_bars

    def find_previous_low_bounce(self, bars: list[dict]) -> float:
        if not bars:
            return 0.0
        candidates = bars[-self.lookback:]
        n = self.neighbor_bars

        for i in range(len(candidates) - 1, -1, -1):
            if i < n or i >= len(candidates) - n:
                continue
            current_low = candidates[i]["low"]
            is_swing = all(
                current_low < candidates[j]["low"]
                for j in range(i - n, i + n + 1)
                if j != i
            )
            if is_swing:
                return current_low

        # Fallback: absolute minimum low in the lookback window
        return min(b["low"] for b in candidates)

    def find_previous_high_bounce(self, bars: list[dict]) -> float:
        if not bars:
            return 0.0
        candidates = bars[-self.lookback:]
        n = self.neighbor_bars

        for i in range(len(candidates) - 1, -1, -1):
            if i < n or i >= len(candidates) - n:
                continue
            current_high = candidates[i]["high"]
            is_swing = all(
                current_high > candidates[j]["high"]
                for j in range(i - n, i + n + 1)
                if j != i
            )
            if is_swing:
                return current_high

        # Fallback: absolute maximum high in the lookback window
        return max(b["high"] for b in candidates)
