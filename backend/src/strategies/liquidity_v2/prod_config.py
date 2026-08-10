from src.strategies.base_strategy import BreakevenConfig
from src.strategies.entry_context import (
    TradingWindow,
    max_bounce_filter,
    min_cross_depth_filter,
    open_trades_limit_filter,
    rollover_filter,
    trading_windows_filter,
)
from src.strategies.liquidity_v2.instrument_params import get_instrument_params
from src.strategies.liquidity_v2.base_strategy import (
    LineRemovalMode,
    StrategyOptions,
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
                              account_configs=None,
                              *, symbol: str) -> StrategyNumbers:
    """
    Numeric parameters that control risk, stop-loss tiers, and entry distance.

    Point-denominated values come from the per-instrument catalog
    (``instrument_params.INSTRUMENT_PARAMS``); ``symbol`` selects which
    instrument's tuned values to use.

    Example – how the tiered SL system works:
        sl_levels = [10, 15, 20, 30]
        If price dipped 18 pts below the line, the system picks 20.0 (smallest
        level >= 18).  If the dip was 35 pts, it falls back to the largest
        level (30.0) because 35 exceeds every tier.
    """
    params = get_instrument_params(symbol)
    return StrategyNumbers(
        # ------------------------------------------------------------------
        # STOP-LOSS TIERING
        # ------------------------------------------------------------------
        # Minimum hard stop in points.  Even if the wick is tiny, the SL
        # will never be tighter than this.
        # Example: min_stop_loss=10.0  ->  smallest possible SL is 10 pts.
        min_stop_loss=params.min_stop_loss,

        # Maximum distance (pts) the price can bounce away from the line
        # AFTER a touch before the line is removed as invalid.
        # Example: line at 100, touch at 100, price rockets to 200 ->
        #          bounce = 100 pts.  100 > max_bounce(90) -> line removed.
        max_bounce=params.max_bounce,

        # Extra padding added to the calculated stop-loss distance.
        # Useful for spreads or to give a little breathing room.
        # Example: calculated SL = 20 pts, extra_sl_space=2.0 -> final SL = 22 pts.
        extra_sl_space=params.extra_sl_space,

        # Tiered stop-loss levels (sorted ascending).  The system auto-picks
        # the smallest level >= distance-to-extreme.  If the extreme is larger
        # than all tiers, it falls back to the largest tier.
        # Example: dip = 25 pts  ->  picks 30.0 (smallest tier >= 25).
        #          dip = 50 pts  ->  falls back to 30.0 (largest tier).
        sl_levels=list(params.sl_levels),

        # How many points ABOVE the chosen SL tier the price can go before
        # the next larger tier is selected.  Prevents flickering between tiers.
        # Example: dip = 30.1 pts, tolerance=3  ->  still uses 30.0 tier
        #          (largest tier, nothing larger to jump to).
        sl_level_tolerance=params.sl_level_tolerance,

        # Extra room (pts) the SL must have beyond the observed sweep
        # extreme.  With 0 the SL can sit right at the extreme, so a
        # marginally deeper retest wicks the trade out.
        # Example: dip = 13 pts, buffer = 5 -> tier must cover 18 -> 20.0.
        sl_buffer_pts=params.sl_buffer_pts,

        # ------------------------------------------------------------------
        # ENTRY DISTANCE LIMIT
        # ------------------------------------------------------------------
        # Max distance from the line level where an entry is still allowed.
        # This is independent of the SL tiers.  If not set, it defaults to
        # max(sl_levels).  Setting it larger lets you enter on late TSI
        # crosses after a big initial move.
        # Example: line=100, max_entry_distance=80.0 -> entry allowed up to 180.
        #          With the old default (max(sl_levels)=30) entry would die at 130.
        max_entry_distance=params.max_entry_distance,

        # ------------------------------------------------------------------
        # CROSS DEPTH
        # ------------------------------------------------------------------
        # Minimum wick depth (pts past the line) required to consider a touch
        # valid.  Prevents entering on phantom touches with no real liquidity.
        # Example: line=100, wick low=99.5 -> depth=0.5.  If min_cross_depth=5.0,
        #          this touch is ignored (not enough liquidity taken).
        min_cross_depth=params.min_cross_depth,

        # ------------------------------------------------------------------
        # RISK / REWARD & ACCOUNT
        # ------------------------------------------------------------------
        # Risk:Reward ratio for take-profit calculation.
        # TP distance = SL distance * rr_ratio.
        # Example: SL = 20 pts, rr_ratio=5.0 -> TP = 100 pts from entry.
        rr_ratio=rr_ratio,

        # Dollar value per point.  MNQ = $2 per point.
        # Used for position-size and PnL calculations.
        point_value=params.point_value,

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

        # Trade-close classification tolerances (points, per instrument).
        be_threshold_points=params.be_threshold_points,
        sl_tp_tolerance=params.sl_tp_tolerance,
    )


def get_prod_candle_config() -> CandleConfig:
    """Candle-pattern recognition thresholds (hammers, wicks, body ratios)."""
    return CandleConfig()


def get_prod_strategy_options(max_bounce: float,
                                min_cross_depth: float,
                                skip_rollover_days: bool,
                                reentry_only: bool,
                                line_removal_mode: LineRemovalMode,
                                max_reentry_attempts: int,
                                trading_windows: list[TradingWindow] | None = None,
                                *, symbol: str) -> StrategyOptions:
    """
    High-level strategy behaviour: filters, triggers, breakeven, re-entry.

    :param trading_windows: session windows, each with its own open-trades limit
        and max number of INITIAL entries (re-entries never consume window slots;
        each initial trade keeps its own re-entry chain). Defaults to the
        instrument's catalog windows (``instrument_params``).
    :param symbol: instrument whose point-denominated trigger/re-entry values
        (velocity thresholds, post-cross distance, re-entry threshold) to use.
    """
    params = get_instrument_params(symbol)
    windows = trading_windows or list(params.trading_windows)
    return StrategyOptions(
        # When to remove a line from active tracking.
        # ON_EVALUATE = remove after processing a bar (default).
        line_removal_mode=line_removal_mode,

        # ------------------------------------------------------------------
        # ENTRY FILTERS  (all must pass for a trade to be considered)
        # ------------------------------------------------------------------
        # ORDER MATTERS: min_cross_depth is a "hold" filter — when it blocks,
        # the line stays alive so depth can accumulate and the trigger can
        # re-fire later.  It must run BEFORE the hard-block trading-windows
        # filter, otherwise a pre-session trigger with shallow depth would
        # kill the line instead of holding it (legacy behavior: time_range
        # ran after min_cross_depth for the same reason).
        entry_filters=[
            # Enforce min_cross_depth (see StrategyNumbers above).
            min_cross_depth_filter(min_cross_depth),

            # Enforce max_bounce (see StrategyNumbers above).
            max_bounce_filter(max_bounce),

            # Global concurrent-open-trades cap (per instrument, from the
            # catalog). Applies across all windows, to initial entries and
            # re-entries alike; this is what allows up to N concurrent trades
            # when their active legs live in DIFFERENT session windows.
            open_trades_limit_filter(params.max_open_trades),

            # Only trade inside the configured windows; each window caps
            # concurrent open trades whose current leg opened inside it
            # (re-entries included) and the number of INITIAL entries
            # (re-entries excluded).
            # (Auto-detects timezone from pair, e.g. MNQ -> NY).
            trading_windows_filter(windows),

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
            # Regime thresholds (per-instrument, pts/min):
            #   abs_vel > fast_threshold -> FAST
            #   abs_vel > slow_threshold -> MODERATE
            #   abs_vel <= slow_threshold -> SLOW
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
                fast_threshold=params.fast_threshold,    # pts/min. Above this = FAST regime.
                slow_threshold=params.slow_threshold,    # pts/min. Between slow-fast = MODERATE.
                lookback=30,            # number of 1m bars for volatility calc
                fast=    [TsiCrossCondition("5m", 2)],   # 2x 5m crosses
                moderate=[TsiCrossCondition("1m", 1), TsiCrossCondition("3m", 1)],   # 1x 1m cross
                slow=    [TsiCrossCondition("1m", 1)],   # 1x 1m cross
                # For double-cross setups: if price moves more than this many
                # points from the line AFTER the first cross, invalidate the line.
                post_cross1_max_dist=params.post_cross1_max_dist,
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
        # the line (in the trade direction) after the SL.  Per instrument.
        # Example: line=100, long SL at 80.  Price drops to 5 ->
        #          95 pts past line.  95 > reentry_threshold(90) -> no re-entry.
        reentry_threshold=params.reentry_threshold,

        # If True, skip the FIRST touch/trigger entirely and ONLY trade
        # re-entries after a stop-loss.
        reentry_only=reentry_only,

        # How many re-entry attempts are allowed per original trade setup.
        # 1 = one re-entry (default), 3 = up to three re-entries, etc.
        max_reentry_attempts=max_reentry_attempts,
    )
