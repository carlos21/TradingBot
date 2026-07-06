"""Strategy factory — centralises instantiation so ``app_factory.py``
doesn't need to hard-code strategy classes.
"""

from __future__ import annotations

from typing import Any

from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
from src.strategies.tsi_cross.config import TsiCrossNumbers
from src.strategies.tsi_cross.strategy import TsiCrossStrategy


class StrategyFactory:
    """Creates strategy instances by name."""

    @staticmethod
    def create(name: str, **kwargs: Any):
        """Instantiate the strategy identified by *name*.

        Supported names:
          - ``"liquidity_v2"`` → ``LiquidityStrategyV2``
          - ``"tsi_cross"``    → ``TsiCrossStrategy``

        All other kwargs are forwarded to the strategy constructor.
        For ``tsi_cross``, V2-specific kwargs (``line_repository``, ``options``,
        etc.) are filtered out or converted automatically.
        """
        supported = {"liquidity_v2", "tsi_cross"}
        if name not in supported:
            raise ValueError(
                f"Unsupported strategy name: {name!r}. "
                f"Supported names: {sorted(supported)}"
            )

        if name == "tsi_cross":
            return StrategyFactory._create_tsi_cross(**kwargs)

        # liquidity_v2 — filter out tsi-cross-specific kwargs
        tsi_keys = {"close_on_opposite_cross", "config"}
        v2_kwargs = {k: v for k, v in kwargs.items() if k not in tsi_keys}
        return LiquidityStrategyV2(**v2_kwargs)

    @staticmethod
    def _create_tsi_cross(
        *,
        min_stop_loss: float = 10.0,
        extra_sl_space: float = 0.0,
        fixed_stop_loss: float | None = None,
        max_stop_loss: float | None = None,
        sl_levels: list[float] | None = None,
        rr_ratio: float = 5.0,
        point_value: float = 2.0,
        account_balance: float = 100000.0,
        risk_per_trade: float | None = None,
        risk_pct_per_trade: float | None = None,
        close_on_opposite_cross: bool = False,
        use_fractional_lots: bool = False,
        fee_per_rt: float = 4.24,
        broker_spread: float = 0.0,
        event_publisher=None,
        trade_repository=None,
        trade_manager=None,
        trade_logger=None,
        analytics=None,
        logger=None,
        account_configs=None,
        accounts_repo=None,
        options=None,
        config=None,
        live_mode: bool = False,
        execution_context=None,
        **kwargs: Any,
    ) -> TsiCrossStrategy:
        """Build a ``TsiCrossStrategy`` with sensible defaults."""
        numbers = TsiCrossNumbers(
            min_stop_loss=min_stop_loss,
            extra_sl_space=extra_sl_space,
            fixed_stop_loss=fixed_stop_loss,
            max_stop_loss=max_stop_loss,
            sl_levels=sl_levels,
            rr_ratio=rr_ratio,
            point_value=point_value,
            account_balance=account_balance,
            risk_per_trade=risk_per_trade,
            risk_pct_per_trade=risk_pct_per_trade,
            use_fractional_lots=use_fractional_lots,
            fee_per_rt=fee_per_rt,
            broker_spread=broker_spread,
            close_on_opposite_cross=close_on_opposite_cross,
        )

        # Derive TsiCrossConfig from StrategyOptions if no explicit config given
        if config is None and options is not None:
            from src.strategies.tsi_cross.config import TsiCrossConfig
            config = TsiCrossConfig(
                entry_filters=list(getattr(options, 'entry_filters', None) or []),
                breakeven=getattr(options, 'breakeven', None),
            )

        return TsiCrossStrategy(
            numbers=numbers,
            config=config,
            event_publisher=event_publisher,
            trade_repository=trade_repository,
            trade_manager=trade_manager,
            trade_logger=trade_logger,
            analytics=analytics,
            logger=logger,
            account_configs=account_configs,
            accounts_repo=accounts_repo,
            live_mode=live_mode,
            execution_context=execution_context,
        )
