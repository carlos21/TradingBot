"""Policies that decide when the strategy indicators are warm."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.readiness.protocols import IWarmupPolicy

if TYPE_CHECKING:
    from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2


class MinimumBarsWarmupPolicy(IWarmupPolicy):
    """
    Indicator warm-up policy based on a minimum bar count per timeframe.

    The strategy's triggers need a continuous history of closes to compute
    TSI and other indicators.  This policy requires at least *min_bars* of
    history for every internal timeframe the strategy aggregates.

    For higher timeframes (e.g. 1h) a single trading session may not contain
    enough bars to reach *min_bars*.  When the available data is exhausted
    (no more historical bars exist) the policy accepts a lower floor so that
    trading is not blocked for hours waiting on live bars.
    """

    def __init__(self, min_bars: int = 30, min_bars_floor: int = 20) -> None:
        if min_bars <= 0:
            raise ValueError("min_bars must be positive")
        if min_bars_floor <= 0:
            raise ValueError("min_bars_floor must be positive")
        # The floor cannot exceed the required minimum; otherwise a low-min_bars
        # configuration (e.g. tests or small timeframes) would be impossible to
        # satisfy.
        self._min_bars = min_bars
        self._min_bars_floor = min(min_bars_floor, min_bars)

    def is_warm(self, strategy: LiquidityStrategyV2) -> bool:
        for tf in strategy.internal_timeframes:
            available = len(strategy.get_history(tf, self._min_bars))
            if available >= self._min_bars:
                continue

            # Check if history is exhausted for this timeframe (requesting
            # one more bar returns the same count → no more data exists).
            total = len(strategy.get_history(tf, self._min_bars + 1))
            data_limited = (total == available)

            if data_limited and available >= self._min_bars_floor:
                if strategy.logger:
                    strategy.logger.info(
                        f"[Warmup] {tf} data-limited: {available}/{self._min_bars} bars "
                        f"(accepted, above floor of {self._min_bars_floor})"
                    )
                continue

            if strategy.logger:
                strategy.logger.info(
                    f"[Warmup] {tf} not warm yet: {available}/{self._min_bars} bars"
                )
            return False
        return True
