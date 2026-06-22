"""Strategy factory for dependency inversion.

App factory depends on this abstraction instead of instantiating
LiquidityStrategyV2 directly, satisfying the Dependency Inversion Principle.
"""

from __future__ import annotations

from typing import Any

from src.application.ports import EventPublisher
from src.financial_calc import FinancialCalc
from src.services.trade_manager import TradeManager
from src.strategies.base_liquidity_strategy import StrategyOptions
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2
from src.strategies.protocols import LiquidityStrategy
from src.strategies.strategy_config import CandleConfig, StrategyNumbers
from src.utils.app_logger import ILogger


def create_liquidity_strategy_v2(
    numbers: StrategyNumbers,
    event_publisher: EventPublisher,
    repos: Any,
    trade_manager: TradeManager,
    options: StrategyOptions | None = None,
    timeframes: list[str] | None = None,
    candle_config: CandleConfig | None = None,
    trade_logger: Any = None,
    analytics: Any = None,
    logger: ILogger | None = None,
    use_fractional_lots: bool = False,
    fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
    broker_spread: float = 0.0,
    accounts_repo: Any = None,
    live_mode: bool = False,
) -> LiquidityStrategy:
    """Create and configure the default liquidity strategy (V2).

    Returns the strategy typed as the ``LiquidityStrategy`` protocol so
    callers depend on an abstraction, not the concrete class.
    """
    return LiquidityStrategyV2(
        min_stop_loss=numbers.min_stop_loss,
        max_bounce=numbers.max_bounce,
        event_publisher=event_publisher,
        line_repository=repos.lines,
        trade_repository=repos.trades,
        trade_manager=trade_manager,
        extra_sl_space=numbers.extra_sl_space,
        fixed_stop_loss=numbers.fixed_stop_loss,
        max_stop_loss=numbers.max_stop_loss,
        sl_levels=numbers.sl_levels,
        max_entry_distance=numbers.max_entry_distance,
        sl_level_tolerance=numbers.sl_level_tolerance,
        min_cross_depth=numbers.min_cross_depth,
        rr_ratio=numbers.rr_ratio,
        point_value=numbers.point_value,
        account_balance=numbers.account_balance,
        risk_per_trade=numbers.risk_per_trade,
        risk_pct_per_trade=numbers.risk_pct_per_trade,
        use_fractional_lots=use_fractional_lots,
        fee_per_rt=fee_per_rt,
        broker_spread=broker_spread,
        options=options,
        timeframes=timeframes,
        candle_config=candle_config,
        trade_logger=trade_logger,
        analytics=analytics,
        trigger_state_repo=repos.trigger_state,
        logger=logger,
        decision_log_repository=repos.decision_logs,
        account_configs=numbers.account_configs,
        accounts_repo=accounts_repo,
        live_mode=live_mode,
    )
