from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class StrategyNumbers:
    """
    Configuration for numeric strategy parameters.
    Moved here from app_factory.py to allow shared usage.
    """
    min_stop_loss: float
    max_bounce: float
    extra_sl_space: float
    fixed_stop_loss: float | None = None
    max_stop_loss: float | None = None
    # Tiered SL levels (sorted ascending). When set, the system picks
    # the smallest level >= distance-to-extreme, falling back to the largest.
    sl_levels: list[float] | None = None
    sl_level_tolerance: float = 5.0
    # Extra breathing room (pts) required beyond the observed sweep extreme
    # when picking an SL tier. 0 = tier may sit right at (or within
    # sl_level_tolerance of) the extreme.
    sl_buffer_pts: float = 0.0
    min_cross_depth: float = 0.0
    rr_ratio: float = 5.0  # Risk:Reward ratio for TP calculation
    point_value: float = 2.0         # dollar value per point (MNQ = $2)
    account_balance: float = 50000.0  # account balance for risk % calculation
    risk_per_trade: float | None = None  # fixed $ risk per trade (from RISK env var)
    risk_pct_per_trade: float | None = None  # % of account to risk per trade
    max_entry_distance: float | None = None  # max pts from line for entry (defaults to max sl_levels)
    account_configs: list = field(default_factory=list)  # list[AccountConfig]
    be_threshold_points: float = 2.0  # exit within this many pts of entry is BE
    sl_tp_tolerance: float = 0.5      # pts tolerance for SL/TP hit detection
    close_on_opposite_cross: bool = False  # tsi_cross: close on opposite TSI cross instead of fixed TP

@dataclass
class CandleConfig:
    """Central configuration for candle pattern recognition ratios."""
    small_body_max_ratio: float = 0.25
    wick_min_ratio: float = 0.60
    big_body_min_ratio: float = 0.40
    hammer_body_max_ratio: float = 0.30
    hammer_nose_max_ratio: float = 0.25
