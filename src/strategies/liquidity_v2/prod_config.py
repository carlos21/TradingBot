from src.strategies.base_strategy import BreakevenConfig
from src.strategies.liquidity_v2.base_strategy import (
    LineRemovalMode,
    StrategyOptions,
)
from src.strategies.entry_context import (
    daily_trades_limit_filter,
    max_bounce_filter,
    min_cross_depth_filter,
    open_trades_limit_filter,
    rollover_filter,
    time_range_filter,
)
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.triggers import (
    TsiCrossCondition,
    VelocityTriggerConfig,
    make_velocity_adaptive_tsi_trigger,
)


def get_prod_strategy_numbers(rr_ratio: float,
                              risk_per_trade: float = None,
                              risk_pct_per_trade: float = None,
                              account_configs=None) -> StrategyNumbers:
    """
    Numeric parameters that control risk, stop-loss tiers, and entry distance.

    Example – how the tiered SL system works:
        sl_levels = [15, 20, 30, 40]
        If price dipped 18 pts below the line, the system picks 20.0 (smallest
        level >= 18).  If the dip was 45 pts, it falls back to the largest
        level (40.0) because 45 exceeds every tier.
    """
    return StrategyNumbers(
        # ------------------------------------------------------------------
        # STOP-LOSS TIERING
        # ------------------------------------------------------------------
        # Minimum hard stop in points.  Even if the wick is tiny, the SL
        # will never be tighter than this.
        # Example: min_stop_loss=10.0  ->  smallest possible SL is 10 pts.
        min_stop_loss=10.0,

        # Maximum distance (pts) the price can bounce away from the line
        # AFTER a touch before the line is removed as invalid.
        # Example: line at 100, touch at 100, price rockets to 200 ->
        #          bounce = 100 pts.  100 > max_bounce(90) -> line removed.
        max_bounce=90.0,

        # Extra padding added to the calculated stop-loss distance.
        # Useful for spreads or to give a little breathing room.
        # Example: calculated SL = 20 pts, extra_sl_space=2.0 -> final SL = 22 pts.
        extra_sl_space=0.0,

        # Tiered stop-loss levels (sorted ascending).  The system auto-picks
        # the smallest level >= distance-to-extreme.  If the extreme is larger
        # than all tiers, it falls back to the largest tier.
        # Example: dip = 25 pts  ->  picks 30.0 (smallest tier >= 25).
        #          dip = 50 pts  ->  falls back to 40.0 (largest tier).
        sl_levels=[15.0, 20.0, 30.0, 40.0],

        # How many points ABOVE the chosen SL tier the price can go before
        # the next larger tier is selected.  Prevents flickering between tiers.
        # Example: dip = 30.1 pts, tolerance=3  ->  still uses 30.0 tier
        #          (would need >= 33.1 to jump to 40.0).
        sl_level_tolerance=3,

        # ------------------------------------------------------------------
        # ENTRY DISTANCE LIMIT
        # ------------------------------------------------------------------
        # Max distance from the line level where an entry is still allowed.
        # This is independent of the SL tiers.  If not set, it defaults to
        # max(sl_levels).  Setting it larger lets you enter on late TSI
        # crosses after a big initial move.
        # Example: line=100, max_entry_distance=80.0 -> entry allowed up to 180.
        #          With the old default (max(sl_levels)=40) entry would die at 140.
        max_entry_distance=50.0,

        # ------------------------------------------------------------------
        # CROSS DEPTH
        # ------------------------------------------------------------------
        # Minimum wick depth (pts past the line) required to consider a touch
        # valid.  Prevents entering on phantom touches with no real liquidity.
        # Example: line=100, wick low=99.5 -> depth=0.5.  If min_cross_depth=5.0,
        #          this touch is ignored (not enough liquidity taken).
        min_cross_depth=5.0,

        # ------------------------------------------------------------------
        # RISK / REWARD & ACCOUNT
        # ------------------------------------------------------------------
        # Risk:Reward ratio for take-profit calculation.
        # TP distance = SL distance * rr_ratio.
        # Example: SL = 20 pts, rr_ratio=5.0 -> TP = 100 pts from entry.
        rr_ratio=rr_ratio,

        # Dollar value per point.  MNQ = $2 per point.
        # Used for position-size and PnL calculations.
        point_value=2.0,

        # Starting account balance in dollars.  Used when calculating risk-%
        # based position sizing.
        account_balance=100000.0,

        # Fixed dollar risk per trade (overrides risk-% if set).
        # Example: risk_per_trade=1000.0 -> each trade risks ~$1000.
        risk_per_trade=risk_per_trade,

        # Percentage of account balance to risk per trade.
        # Example: risk_pct_per_trade=1.0 -> 1% of $100k = $1,000 risk.
        risk_pct_per_trade=risk_pct_per_trade,

        # NinjaTrader account configurations for multi-account execution.
        account_configs=account_configs or [],
    )


def get_prod_candle_config() -> CandleConfig:
    """Candle-pattern recognition thresholds (hammers, wicks, body ratios)."""
    return CandleConfig()


def get_prod_strategy_options(max_bounce: float,
                                min_cross_depth: float = 0.0,
                                skip_rollover_days: bool = False,
                                reentry_only: bool = False,
                                line_removal_mode: LineRemovalMode = LineRemovalMode.ON_EVALUATE) -> StrategyOptions:
    """
    High-level strategy behaviour: filters, triggers, breakeven, re-entry.
    """
    return StrategyOptions(
        # When to remove a line from active tracking.
        # ON_EVALUATE = remove after processing a bar (default).
        line_removal_mode=line_removal_mode,

        # ------------------------------------------------------------------
        # ENTRY FILTERS  (all must pass for a trade to be considered)
        # ------------------------------------------------------------------
        entry_filters=[
            # Allow only 1 open trade at a time per line direction.
            open_trades_limit_filter(1),

            # Enforce min_cross_depth (see StrategyNumbers above).
            min_cross_depth_filter(min_cross_depth),

            # Enforce max_bounce (see StrategyNumbers above).
            max_bounce_filter(max_bounce),

            # Only trade between 08:00 and 15:30 America/New_York.
            # (Auto-detects timezone from pair, e.g. MNQ -> NY).
            time_range_filter("08:00", "15:30"),

            # Hard cap: max 1 trade per day.
            daily_trades_limit_filter(max_trades_per_day=1),

            # Skip days near futures rollover (if enabled).
            rollover_filter(enabled=skip_rollover_days),
        ],

        # ------------------------------------------------------------------
        # TRIGGERS  (OR logic – first trigger that fires wins)
        # ------------------------------------------------------------------
        triggers=[
            # Velocity-adaptive TSI trigger.
            # Measures recent bar-range volatility and picks a TSI confirmation
            # regime based on how fast price is moving.
            #
            # Volatility calculation (_calculate_volatility_score):
            #   - lookback=30  -> uses last 30 one-minute bars
            #   - Groups bars into 5-minute chunks
            #   - Sum of max(high)-min(low) per chunk, divided by 30
            #   - Result = avg bar-range per minute (pts/min)
            #
            # Regime thresholds:
            #   abs_vel > fast_threshold(28.0)     -> FAST
            #   abs_vel > slow_threshold(15.0)     -> MODERATE
            #   abs_vel <= slow_threshold(15.0)    -> SLOW
            #
            # Regime requirements:
            #   FAST     -> need 2 TSI crosses on 5m  (double confirmation)
            #   MODERATE -> need 1 TSI cross on 1m
            #   SLOW     -> need 1 TSI cross on 1m
            #
            # The regime is LOCKED at the moment of line touch and never
            # re-evaluated, so the same requirement applies for the entire
            # lifetime of the line.
            make_velocity_adaptive_tsi_trigger(VelocityTriggerConfig(
                fast_threshold=28.0,    # pts/min.  Above this = FAST regime.
                slow_threshold=15.0,    # pts/min.  Between 15-28 = MODERATE.
                lookback=30,            # number of 1m bars for volatility calc
                fast=    [TsiCrossCondition("5m", 2)],   # 2x 5m crosses
                moderate=[TsiCrossCondition("3m", 1)],   # 1x 1m cross
                slow=    [TsiCrossCondition("1m", 1)],   # 1x 1m cross
                # For double-cross setups: if price moves >80 pts from the
                # line AFTER the first cross, invalidate the line entirely.
                post_cross1_max_dist=80.0,
            )),

            # ----------------------------------------------------------------
            # ALTERNATIVE TRIGGERS (commented out – uncomment to activate)
            # ----------------------------------------------------------------
            # Classic TSI cross on 5m or 15m only (no velocity adaptation).
            # trigger_with_timeframes(tsi_cross_trigger, ['5m', '15m']),

            # 3-candle reversal pattern on 5m/15m/30m/1h.
            # trigger_with_timeframes(three_candle_reversal_trigger, ['5m', '15m','30m', '1h']),

            # Double 5m close-above/close-below pattern.
            # trigger_with_timeframes(double_5m_cross_trigger, ['5m']),

            # Wick-touch confirmation (hammer-like) on 5m/15m.
            # trigger_with_timeframes(wick_near_line_trigger, ['5m', '15m'])
        ],

        # ------------------------------------------------------------------
        # BREAKEVEN
        # ------------------------------------------------------------------
        # Classic breakeven (move SL to entry once RR reached).
        # None = disabled.
        breakeven=None,

        # Re-entry breakeven: after a stop-loss, if price comes back and
        # triggers again, move the new trade's SL to breakeven very early.
        # trigger_rr=2.0  -> when unrealized RR hits 2.0, move SL to entry.
        # move_to_rr=0.05 -> actually move it to +0.05 RR (tiny profit).
        reentry_breakeven=BreakevenConfig(
            trigger_rr=2.0,
            move_to_rr=0.05
        ),

        # ------------------------------------------------------------------
        # RE-ENTRY RULES
        # ------------------------------------------------------------------
        # If a trade hits SL, allow a new entry on the same line if price
        # comes back and triggers again.
        reentry_after_sl=True,

        # Cancel re-entry opportunity if price moves this many points PAST
        # the line (in the trade direction) after the SL.
        # Example: line=100, long SL at 80.  Price drops to 5 ->
        #          95 pts past line.  95 > reentry_threshold(90) -> no re-entry.
        reentry_threshold=90.0,

        # If True, skip the FIRST touch/trigger entirely and ONLY trade
        # re-entries after a stop-loss.
        reentry_only=reentry_only,
    )
