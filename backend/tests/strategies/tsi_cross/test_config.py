"""Tests for src.strategies.tsi_cross.config."""

from dataclasses import fields

from src.strategies.base_strategy import BreakevenConfig
from src.strategies.entry_context import open_trades_limit_filter
from src.strategies.tsi_cross.config import TsiCrossConfig, TsiCrossNumbers


class TestTsiCrossNumbers:

    def test_defaults(self):
        numbers = TsiCrossNumbers()
        assert numbers.min_stop_loss == 10.0
        assert numbers.extra_sl_space == 0.0
        assert numbers.fixed_stop_loss is None
        assert numbers.max_stop_loss is None
        assert numbers.sl_levels is None
        assert numbers.rr_ratio == 5.0
        assert numbers.point_value == 2.0
        assert numbers.account_balance == 100000.0
        assert numbers.risk_per_trade is None
        assert numbers.risk_pct_per_trade is None
        assert numbers.fee_per_rt == 4.24
        assert numbers.broker_spread == 0.0
        assert numbers.use_fractional_lots is False
        assert numbers.close_on_opposite_cross is False

    def test_close_on_opposite_cross_override(self):
        numbers = TsiCrossNumbers(close_on_opposite_cross=True)
        assert numbers.close_on_opposite_cross is True

    def test_all_fields_set(self):
        numbers = TsiCrossNumbers(
            min_stop_loss=5.0,
            extra_sl_space=1.0,
            fixed_stop_loss=15.0,
            max_stop_loss=30.0,
            sl_levels=[10.0, 20.0],
            rr_ratio=3.0,
            point_value=5.0,
            account_balance=50000.0,
            risk_per_trade=100.0,
            risk_pct_per_trade=1.0,
            fee_per_rt=2.5,
            broker_spread=0.5,
            use_fractional_lots=True,
            close_on_opposite_cross=True,
        )
        assert numbers.min_stop_loss == 5.0
        assert numbers.sl_levels == [10.0, 20.0]
        assert numbers.use_fractional_lots is True

    def test_field_count(self):
        assert len(fields(TsiCrossNumbers)) == 14


class TestTsiCrossConfig:

    def test_defaults(self):
        config = TsiCrossConfig()
        assert config.tsi_long_len == 6
        assert config.tsi_short_len == 13
        assert config.tsi_signal_len == 4
        assert config.timeframe == "5m"
        assert config.bounce_lookback == 20
        assert config.bounce_neighbor_bars == 1
        assert config.entry_filters is None
        assert config.breakeven is None
        assert config.cross_confirmation_bars == 0

    def test_custom_values(self):
        be = BreakevenConfig(trigger_rr=2.0)
        config = TsiCrossConfig(
            tsi_long_len=10,
            tsi_short_len=20,
            tsi_signal_len=5,
            timeframe="15m",
            bounce_lookback=30,
            bounce_neighbor_bars=2,
            entry_filters=[open_trades_limit_filter(1)],
            breakeven=be,
            cross_confirmation_bars=3,
        )
        assert config.tsi_long_len == 10
        assert config.timeframe == "15m"
        assert config.breakeven is be
        assert config.cross_confirmation_bars == 3

    def test_field_count(self):
        assert len(fields(TsiCrossConfig)) == 9
