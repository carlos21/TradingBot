from src.strategies.base_liquidity_strategy import BreakevenConfig
from src.strategies.liquidity_strategy import StrategyOptions, LineRemovalMode
from src.strategies.entry_context import open_trades_limit_filter, max_bounce_filter
from src.strategies.strategy_config import CandleConfig, StrategyNumbers
from src.strategies.triggers import three_candle_reversal_trigger, wick_near_line_trigger, double_5m_cross_trigger
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
            three_candle_reversal_trigger,
            double_5m_cross_trigger,
            wick_near_line_trigger
        ],
        breakeven=BreakevenConfig(
            trigger_rr=2.0, 
            move_to_rr=0.05 
        )
    )