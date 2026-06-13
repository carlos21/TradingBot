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
    """

    def __init__(self, min_bars: int = 30) -> None:
        if min_bars <= 0:
            raise ValueError("min_bars must be positive")
        self._min_bars = min_bars

    def is_warm(self, strategy: LiquidityStrategyV2) -> bool:
        for tf in strategy.internal_timeframes:
            history = strategy.get_history(tf, self._min_bars)
            if len(history) < self._min_bars:
                if strategy.logger:
                    strategy.logger.info(
                        f"[Warmup] {tf} not warm yet: {len(history)}/{self._min_bars} bars"
                    )
                return False
        return True
