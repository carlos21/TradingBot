"""Regression: app_factory must use LiveLiquidityStrategyV2 in live mode."""

from unittest.mock import MagicMock, patch

from app_factory import create_app
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2, LiveLiquidityStrategyV2


def test_live_mode_uses_live_strategy_v2():
    """Live mode must instantiate LiveLiquidityStrategyV2 to avoid local SL/TP checks racing NT."""
    mock_ds = MagicMock()
    mock_repos = MagicMock()
    mock_numbers = MagicMock()
    mock_numbers.account_configs = []
    mock_numbers.min_stop_loss = 5.0
    mock_numbers.max_bounce = 10.0
    mock_numbers.extra_sl_space = 0.0
    mock_numbers.fixed_stop_loss = None
    mock_numbers.max_stop_loss = None
    mock_numbers.sl_levels = None
    mock_numbers.sl_level_tolerance = 5.0
    mock_numbers.min_cross_depth = 0.0
    mock_numbers.rr_ratio = 5.0
    mock_numbers.point_value = 2.0
    mock_numbers.account_balance = 100000.0
    mock_numbers.risk_per_trade = None
    mock_numbers.risk_pct_per_trade = None
    mock_numbers.use_fractional_lots = False
    mock_numbers.fee_per_rt = 2.5
    mock_numbers.broker_spread = 0.0

    with patch("app_factory.LiquidityStrategyV2") as mock_cls, \
         patch("app_factory.LiveLiquidityStrategyV2") as mock_live_cls:
        mock_live_cls.return_value = MagicMock(spec=LiveLiquidityStrategyV2)
        mock_cls.return_value = MagicMock(spec=LiquidityStrategyV2)

        create_app(
            pair="MNQ",
            data_source=mock_ds,
            repos=mock_repos,
            numbers=mock_numbers,
            live_mode=True,
        )

        assert mock_live_cls.called, "Live mode must use LiveLiquidityStrategyV2"
        assert not mock_cls.called, "Live mode must NOT use base LiquidityStrategyV2"


def test_non_live_mode_uses_base_strategy_v2():
    """Non-live mode should continue using LiquidityStrategyV2."""
    mock_ds = MagicMock()
    mock_repos = MagicMock()
    mock_numbers = MagicMock()
    mock_numbers.account_configs = []
    mock_numbers.min_stop_loss = 5.0
    mock_numbers.max_bounce = 10.0
    mock_numbers.extra_sl_space = 0.0
    mock_numbers.fixed_stop_loss = None
    mock_numbers.max_stop_loss = None
    mock_numbers.sl_levels = None
    mock_numbers.sl_level_tolerance = 5.0
    mock_numbers.min_cross_depth = 0.0
    mock_numbers.rr_ratio = 5.0
    mock_numbers.point_value = 2.0
    mock_numbers.account_balance = 100000.0
    mock_numbers.risk_per_trade = None
    mock_numbers.risk_pct_per_trade = None
    mock_numbers.use_fractional_lots = False
    mock_numbers.fee_per_rt = 2.5
    mock_numbers.broker_spread = 0.0

    with patch("app_factory.LiquidityStrategyV2") as mock_cls, \
         patch("app_factory.LiveLiquidityStrategyV2") as mock_live_cls:
        mock_live_cls.return_value = MagicMock(spec=LiveLiquidityStrategyV2)
        mock_cls.return_value = MagicMock(spec=LiquidityStrategyV2)

        create_app(
            pair="MNQ",
            data_source=mock_ds,
            repos=mock_repos,
            numbers=mock_numbers,
            live_mode=False,
        )

        assert mock_cls.called, "Non-live mode must use LiquidityStrategyV2"
        assert not mock_live_cls.called, "Non-live mode must NOT use LiveLiquidityStrategyV2"
