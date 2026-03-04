from src.strategies.base_liquidity_strategy import BreakevenConfig, StrategyOptions, LineRemovalMode
from src.strategies.entry_context import daily_trades_limit_filter, open_trades_limit_filter, max_bounce_filter, time_range_filter
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
)
import os

def get_prod_strategy_numbers() -> StrategyNumbers:
    return StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=65.0,
        extra_sl_space=0.0,
        fixed_stop_loss=20.0,
        max_stop_loss=40
    )

def get_prod_candle_config() -> CandleConfig:
    return CandleConfig()

def get_prod_strategy_options(max_bounce: float) -> StrategyOptions:
    removal_mode_env = os.environ.get("LINE_REMOVAL_MODE", "ON_EVALUATE").upper()
    removal_mode = LineRemovalMode.NEVER if removal_mode_env == "NEVER" else LineRemovalMode.ON_EVALUATE

    return StrategyOptions(
        line_removal_mode=removal_mode,
        entry_filters=[
            open_trades_limit_filter(1), 
            max_bounce_filter(max_bounce),
            # It will now automatically detect NQ -> America/New_York
            time_range_filter("08:00", "14:00"),
            daily_trades_limit_filter(max_trades_per_day=1)
        ],
        triggers=[
            make_velocity_adaptive_tsi_trigger(VelocityTriggerConfig(
                fast_threshold=3.0,
                slow_threshold=1.0,
                lookback=5,
                fast=    [TsiCrossCondition("5m", 2)],
                moderate=[TsiCrossCondition("1m", 2), TsiCrossCondition("3m", 1)],
                slow=    [TsiCrossCondition("1m", 2), TsiCrossCondition("3m", 1)],
            )),
            # trigger_with_timeframes(tsi_cross_trigger, ['5m', '15m']),
            # trigger_with_timeframes(three_candle_reversal_trigger, ['5m', '15m','30m', '1h']),
            # trigger_with_timeframes(double_5m_cross_trigger, ['5m']),
            # trigger_with_timeframes(wick_near_line_trigger, ['5m', '15m'])
        ],
        breakeven=BreakevenConfig(
            trigger_rr=2.0, 
            move_to_rr=0.05 
        )
    )