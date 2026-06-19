"""Tests for src.strategies.strategy_factory."""

import pytest

from src.strategies.strategy_factory import StrategyFactory
from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from src.strategies.tsi_cross.strategy import TsiCrossStrategy


class TestStrategyFactory:

    def test_create_liquidity_v2(self):
        strategy = StrategyFactory.create(
            "liquidity_v2",
            min_stop_loss=10.0,
            max_bounce=5.0,
            event_publisher=None,
            line_repository=None,
            trade_repository=None,
            trade_manager=None,
            extra_sl_space=0.0,
            point_value=2.0,
            account_balance=100000.0,
            options=DEFAULT_STRATEGY_OPTIONS,
        )
        assert isinstance(strategy, LiquidityStrategyV2)

    def test_create_tsi_cross(self):
        strategy = StrategyFactory.create("tsi_cross")
        assert isinstance(strategy, TsiCrossStrategy)

    def test_create_unknown_name_raises(self):
        with pytest.raises(ValueError, match="Unsupported strategy name"):
            StrategyFactory.create("unknown_strategy")

    def test_create_liquidity_v2_filters_tsi_kwargs(self):
        """tsi_cross kwargs must not be passed to LiquidityStrategyV2."""
        strategy = StrategyFactory.create(
            "liquidity_v2",
            min_stop_loss=10.0,
            max_bounce=5.0,
            event_publisher=None,
            line_repository=None,
            trade_repository=None,
            trade_manager=None,
            extra_sl_space=0.0,
            point_value=2.0,
            account_balance=100000.0,
            options=DEFAULT_STRATEGY_OPTIONS,
            close_on_opposite_cross=True,
            config={"foo": "bar"},
        )
        assert isinstance(strategy, LiquidityStrategyV2)
