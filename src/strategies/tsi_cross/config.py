from dataclasses import dataclass

from src.strategies.base_strategy import BreakevenConfig
from src.strategies.entry_context import EntryFilter


@dataclass
class TsiCrossNumbers:
    """Numeric parameters — SL, risk, position sizing.

    Mirrors the ``StrategyNumbers`` pattern in ``src/prod_config.py`` so the
    strategy is easy to tweak in one place.
    """

    # Minimum hard stop in points
    min_stop_loss: float = 10.0

    # Extra padding added to the calculated stop-loss (points)
    extra_sl_space: float = 0.0

    # Fixed SL distance (overrides dynamic calculation when set)
    fixed_stop_loss: float | None = None

    # Max cap on SL distance
    max_stop_loss: float | None = None

    # Tiered SL levels (smallest level >= distance is chosen)
    sl_levels: list[float] | None = None

    # Risk:Reward ratio for take-profit
    rr_ratio: float = 5.0

    # Dollar value per point (MNQ = $2)
    point_value: float = 2.0

    # Starting account balance
    account_balance: float = 100000.0

    # Fixed dollar risk per trade (overrides risk-%)
    risk_per_trade: float | None = None

    # Percentage of account balance to risk per trade
    risk_pct_per_trade: float | None = None

    # Broker fee per round-trip per contract
    fee_per_rt: float = 4.24

    # Spread in points (CFD mode)
    broker_spread: float = 0.0

    # Use fractional lots (micro-lots) instead of whole contracts
    use_fractional_lots: bool = False

    # If True, trade closes at the next opposite TSI cross instead of a fixed TP.
    # Example: long entered on bullish cross → closed on next bearish cross.
    # When enabled, take_profit is set to None and rr_ratio is ignored for exit.
    close_on_opposite_cross: bool = False


@dataclass
class TsiCrossConfig:
    """High-level behaviour — TSI params, bounce detection, filters.

    This is the user's primary tweaking surface.  Change TSI lengths, bounce
    lookback, or swap the filter list without touching strategy logic.
    """

    # TSI calculation lengths
    tsi_long_len: int = 6
    tsi_short_len: int = 13
    tsi_signal_len: int = 4

    # Timeframe to aggregate bars to before running TSI
    timeframe: str = "5m"

    # How many bars to look back when searching for a bounce
    bounce_lookback: int = 20

    # How many neighbour bars each side to confirm a local extremum
    bounce_neighbor_bars: int = 1

    # Pluggable entry filters (same pipeline as LiquidityStrategyV2)
    entry_filters: list[EntryFilter] | None = None

    # Breakeven config (None = disabled)
    breakeven: BreakevenConfig | None = None

    # Minimum consecutive bars TSI must be on the "from" side before a cross
    # is considered valid. 0 = disabled (strict cross only).
    cross_confirmation_bars: int = 0
