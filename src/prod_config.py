from src.strategies.base_liquidity_strategy import BreakevenConfig
from src.strategies.liquidity_strategy import StrategyOptions, LineRemovalMode
from src.strategies.entry_context import open_trades_limit_filter, max_bounce_filter
from src.strategies.strategy_config import CandleConfig, StrategyNumbers
from src.strategies.triggers import (
    three_candle_reversal_trigger, 
    wick_near_line_trigger,
    double_5m_cross_trigger,
    trigger_with_timeframes
)
import os

def get_prod_strategy_numbers() -> StrategyNumbers:
    return StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=65.0,
        extra_sl_space=0.0,
    )

def get_prod_candle_config() -> CandleConfig:
    return CandleConfig()

def get_prod_strategy_options(max_bounce: float) -> StrategyOptions:
    # Allow env override for testing specific behaviors if needed, 
    # but default to production settings.
    removal_mode_env = os.environ.get("LINE_REMOVAL_MODE", "ON_EVALUATE").upper()
    removal_mode = LineRemovalMode.NEVER if removal_mode_env == "NEVER" else LineRemovalMode.ON_EVALUATE

    return StrategyOptions(
        line_removal_mode=removal_mode,
        entry_filters=[
            open_trades_limit_filter(1), 
            max_bounce_filter(max_bounce)
        ],
        triggers=[
            # 1. Three Candle Reversal -> ONLY 15m
            trigger_with_timeframes(three_candle_reversal_trigger, ['5m', '15m','30m', '1h']),
            
            # 2. Double Cross -> ONLY 5m
            trigger_with_timeframes(double_5m_cross_trigger, ['5m']),
            
            # 3. Wick Near Line -> 5m AND 15m (Lowest Priority)
            trigger_with_timeframes(wick_near_line_trigger, ['5m', '15m'])
        ],
        breakeven=BreakevenConfig(
            trigger_rr=2.0, 
            move_to_rr=0.05 
        )
    )