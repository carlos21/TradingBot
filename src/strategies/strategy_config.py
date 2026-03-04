from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class StrategyNumbers:
    """
    Configuration for numeric strategy parameters.
    Moved here from app_factory.py to allow shared usage.
    """
    min_stop_loss: float
    max_bounce: float
    extra_sl_space: float
    fixed_stop_loss: Optional[float] = None
    max_stop_loss: Optional[float] = None
    # Tiered SL levels (sorted ascending). When set, the system picks
    # the smallest level >= distance-to-extreme, falling back to the largest.
    sl_levels: Optional[List[float]] = None

@dataclass
class CandleConfig:
    """Central configuration for candle pattern recognition ratios."""
    small_body_max_ratio: float = 0.25
    wick_min_ratio: float = 0.60
    big_body_min_ratio: float = 0.40
    hammer_body_max_ratio: float = 0.30
    hammer_nose_max_ratio: float = 0.25