from src.strategies.base_liquidity_strategy import BreakevenConfig, StrategyOptions, LineRemovalMode
from src.strategies.entry_context import daily_trades_limit_filter, open_trades_limit_filter, max_bounce_filter, time_range_filter, min_cross_depth_filter, rollover_filter
from src.strategies.strategy_config import CandleConfig, StrategyNumbers
from src.strategies.triggers import (
    three_candle_reversal_trigger,
    tsi_cross_trigger,
    wick_near_line_trigger,
    double_5m_cross_trigger,
    trigger_with_timeframes,
    make_velocity_adaptive_tsi_trigger,
    VelocityTriggerConfig,
    TsiCrossCondition,
    TsiDivergenceCondition,
)
def get_prod_strategy_numbers(rr_ratio: float,
                              risk_per_trade: float = None,
                              risk_pct_per_trade: float = None) -> StrategyNumbers:
    return StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=90.0,
        extra_sl_space=0.0,
        sl_levels=[15.0, 20.0, 30.0, 40.0],
        sl_level_tolerance=3,
        min_cross_depth=5.0,
        rr_ratio=rr_ratio,
        point_value=2.0,        # MNQ: $2 per point
        account_balance=100000.0,
        risk_per_trade=risk_per_trade,
        risk_pct_per_trade=risk_pct_per_trade,
    )

def get_prod_candle_config() -> CandleConfig:
    return CandleConfig()

def get_prod_strategy_options(max_bounce: float,
                                min_cross_depth: float = 0.0,
                                skip_rollover_days: bool = False,
                                reentry_only: bool = False,
                                line_removal_mode: LineRemovalMode = LineRemovalMode.ON_EVALUATE) -> StrategyOptions:
    return StrategyOptions(
        line_removal_mode=line_removal_mode,
        entry_filters=[
            open_trades_limit_filter(1),
            min_cross_depth_filter(min_cross_depth),
            max_bounce_filter(max_bounce),
            # It will now automatically detect MNQ -> America/New_York
            time_range_filter("08:00", "15:30"),
            daily_trades_limit_filter(max_trades_per_day=1),
            rollover_filter(enabled=skip_rollover_days),
        ],
        triggers=[
            make_velocity_adaptive_tsi_trigger(VelocityTriggerConfig(
                fast_threshold=5.0,   # velocity > 3.0 pts/min  -> need 2 TSI crosses on 5m
                slow_threshold=5.0,   # velocity <= 3.0 pts/min -> considered SLOW
                lookback=30,
                fast=    [TsiCrossCondition("5m", 2)],
                moderate=[TsiCrossCondition("1m", 1), TsiCrossCondition("3m", 1)],
                slow=    [TsiCrossCondition("1m", 1), TsiCrossCondition("3m", 1)],
                post_cross1_max_dist=80.0,  # invalidate if price moves >80pts from line after 1st cross
            )),
            # Example: mix cross and divergence conditions per regime
            # make_velocity_adaptive_tsi_trigger(VelocityTriggerConfig(
            #     fast=[
            #         TsiCrossCondition("5m", 2),
            #         TsiDivergenceCondition("5m", lookback=30),
            #     ],
            #     moderate=[TsiCrossCondition("3m", 1)],
            #     slow=[TsiDivergenceCondition("1m", lookback=20)],
            #     post_cross1_max_dist=80.0,
            # )),
            # trigger_with_timeframes(tsi_cross_trigger, ['5m', '15m']),
            # trigger_with_timeframes(three_candle_reversal_trigger, ['5m', '15m','30m', '1h']),
            # trigger_with_timeframes(double_5m_cross_trigger, ['5m']),
            # trigger_with_timeframes(wick_near_line_trigger, ['5m', '15m'])
        ],
        breakeven=None,
        reentry_breakeven=BreakevenConfig(
            trigger_rr=2.0,
            move_to_rr=0.05
        ),
        reentry_after_sl=True,      # set True to re-enter if price comes back after a SL hit
        reentry_threshold=90.0,      # cancel re-entry if price goes this many pts past the line
        reentry_only=reentry_only,  # skip initial trade, only take re-entry trades
    )