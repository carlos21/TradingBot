from dataclasses import dataclass
from typing import Dict

@dataclass
class StrategyConfig:
    stop_loss:      Dict[str, float]
    max_bounce:     Dict[str, float]
    extra_sl_space: Dict[str, float]

@dataclass
class CandleConfig:
    """Central configuration for candle pattern recognition ratios."""
    small_body_max_ratio: float = 0.25
    wick_min_ratio: float = 0.60
    big_body_min_ratio: float = 0.45
    hammer_body_max_ratio: float = 0.30
    hammer_nose_max_ratio: float = 0.25