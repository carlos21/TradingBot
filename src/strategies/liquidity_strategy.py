from __future__ import annotations

from typing import List

from src.strategies.base_liquidity_strategy import (
    BaseLiquidityStrategy,
    StrategyOptions,
    LineRemovalMode,
)
from src.strategies.entry_context import retest_cross_trigger, EntryTrigger


__all__ = [
    "LiquidityStrategy",
    # Re-export to preserve old imports:
    "StrategyOptions",
    "LineRemovalMode",
]


class LiquidityStrategy(BaseLiquidityStrategy):
    """
    Original behavior:
      - waits for a retest CLOSE back through the line
      - uses the 'extreme' tracked while price was across the level
      - entries at bar close, 4R TP, min SL guard

    Triggers can be overridden via options.triggers; otherwise uses default below.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Use user-provided triggers if given; otherwise our default
        self.triggers = list(self.options.triggers or self.default_triggers())

    def default_triggers(self) -> List[EntryTrigger]:
        return [retest_cross_trigger]