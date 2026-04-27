from dataclasses import dataclass
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
    sl_level_tolerance: float = 5.0
    min_cross_depth: float = 0.0
    rr_ratio: float = 5.0  # Risk:Reward ratio for TP calculation
    point_value: float = 2.0         # dollar value per point (MNQ = $2)
    account_balance: float = 50000.0  # account balance for risk % calculation
    risk_per_trade: Optional[float] = None  # fixed $ risk per trade (from RISK env var)
    risk_pct_per_trade: Optional[float] = None  # % of account to risk per trade (from RISK_PCT env var)

@dataclass
class CandleConfig:
    """Central configuration for candle pattern recognition ratios."""
    small_body_max_ratio: float = 0.25
    wick_min_ratio: float = 0.60
    big_body_min_ratio: float = 0.40
    hammer_body_max_ratio: float = 0.30
    hammer_nose_max_ratio: float = 0.25