"""Tests for src.strategies.strategy_factory."""

from unittest.mock import MagicMock

import pytest

from src.strategies.liquidity_v2.config import StrategyNumbers
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
from src.strategies.strategy_factory import StrategyFactory
from src.strategies.tsi_cross.config import TsiCrossConfig, TsiCrossNumbers
from src.strategies.tsi_cross.strategy import TsiCrossStrategy


class TestStrategyFactoryCreate:

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


class TestStrategyFactoryTsiCrossDefaults:

    def test_tsi_cross_default_numbers(self):
        strategy = StrategyFactory.create("tsi_cross")
        assert strategy._tsi_numbers.min_stop_loss == 10.0
        assert strategy._tsi_numbers.rr_ratio == 5.0
        assert strategy._tsi_numbers.point_value == 2.0
        assert strategy._tsi_numbers.account_balance == 100000.0
        assert strategy._tsi_numbers.close_on_opposite_cross is False

    def test_tsi_cross_numbers_override(self):
        strategy = StrategyFactory.create(
            "tsi_cross",
            min_stop_loss=15.0,
            rr_ratio=3.0,
            point_value=5.0,
            account_balance=50000.0,
            close_on_opposite_cross=True,
        )
        assert strategy._tsi_numbers.min_stop_loss == 15.0
        assert strategy._tsi_numbers.rr_ratio == 3.0
        assert strategy._tsi_numbers.point_value == 5.0
        assert strategy._tsi_numbers.account_balance == 50000.0
        assert strategy._tsi_numbers.close_on_opposite_cross is True

    def test_tsi_cross_explicit_config(self):
        config = TsiCrossConfig(timeframe="15m", tsi_long_len=10)
        strategy = StrategyFactory.create("tsi_cross", config=config)
        assert strategy._tsi_config is config
        assert strategy._tsi_config.timeframe == "15m"

    def test_tsi_cross_config_derived_from_options(self):
        options = MagicMock()
        options.entry_filters = [MagicMock()]
        options.breakeven = MagicMock()
        strategy = StrategyFactory.create("tsi_cross", options=options)
        assert strategy._tsi_config.entry_filters == options.entry_filters
        assert strategy._tsi_config.breakeven is options.breakeven

    def test_tsi_cross_explicit_config_takes_precedence_over_options(self):
        options = MagicMock()
        options.entry_filters = [MagicMock()]
        config = TsiCrossConfig(timeframe="1h")
        strategy = StrategyFactory.create("tsi_cross", options=options, config=config)
        assert strategy._tsi_config is config


class TestStrategyFactoryLiquidityV2Kwargs:

    def test_liquidity_v2_receives_numbers(self):
        strategy = StrategyFactory.create(
            "liquidity_v2",
            min_stop_loss=12.0,
            max_bounce=6.0,
            extra_sl_space=1.0,
            point_value=5.0,
            account_balance=75000.0,
            event_publisher=None,
            line_repository=None,
            trade_repository=None,
            trade_manager=None,
            options=DEFAULT_STRATEGY_OPTIONS,
        )
        assert strategy.min_stop_loss == 12.0
        assert strategy.max_bounce == 6.0
        assert strategy.point_value == 5.0
        assert strategy.account_balance == 75000.0
