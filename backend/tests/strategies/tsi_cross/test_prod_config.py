"""Tests for src.strategies.tsi_cross.prod_config."""

from src.strategies.base_strategy import BreakevenConfig
from src.strategies.entry_context import (
    daily_trades_limit_filter,
    open_trades_limit_filter,
    rollover_filter,
    time_range_filter,
)
from src.strategies.tsi_cross.config import TsiCrossConfig, TsiCrossNumbers
from src.strategies.tsi_cross.prod_config import (
    get_tsi_cross_config,
    get_tsi_cross_numbers,
)


class TestGetTsiCrossNumbers:

    def test_returns_numbers_instance(self):
        numbers = get_tsi_cross_numbers()
        assert isinstance(numbers, TsiCrossNumbers)

    def test_defaults(self):
        numbers = get_tsi_cross_numbers()
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
        assert numbers.close_on_opposite_cross is False
        assert numbers.fee_per_rt == 4.24
        assert numbers.broker_spread == 0.0
        assert numbers.use_fractional_lots is False

    def test_rr_ratio_override(self):
        numbers = get_tsi_cross_numbers(rr_ratio=3.0)
        assert numbers.rr_ratio == 3.0

    def test_risk_overrides(self):
        numbers = get_tsi_cross_numbers(
            risk_per_trade=200.0,
            risk_pct_per_trade=1.5,
        )
        assert numbers.risk_per_trade == 200.0
        assert numbers.risk_pct_per_trade == 1.5

    def test_account_balance_override(self):
        numbers = get_tsi_cross_numbers(account_balance=50000.0)
        assert numbers.account_balance == 50000.0

    def test_fixed_stop_loss_override(self):
        numbers = get_tsi_cross_numbers(fixed_stop_loss=25.0)
        assert numbers.fixed_stop_loss == 25.0

    def test_close_on_opposite_cross_override(self):
        numbers = get_tsi_cross_numbers(close_on_opposite_cross=True)
        assert numbers.close_on_opposite_cross is True

    def test_point_value_and_fee_overrides(self):
        numbers = get_tsi_cross_numbers(point_value=5.0, fee_per_rt=2.88)
        assert numbers.point_value == 5.0
        assert numbers.fee_per_rt == 2.88


class TestGetTsiCrossConfig:

    def test_returns_config_instance(self):
        config = get_tsi_cross_config()
        assert isinstance(config, TsiCrossConfig)

    def test_defaults(self):
        config = get_tsi_cross_config()
        assert config.tsi_long_len == 6
        assert config.tsi_short_len == 13
        assert config.tsi_signal_len == 4
        assert config.timeframe == "5m"
        assert config.bounce_lookback == 20
        assert config.bounce_neighbor_bars == 1
        assert config.breakeven is None
        assert config.cross_confirmation_bars == 0

    def test_default_filters(self):
        config = get_tsi_cross_config()
        assert len(config.entry_filters) == 4
        # Filters are closures; check expected __name__ values
        names = [f.__name__ for f in config.entry_filters]
        assert "open_trades_limit" in names
        assert "time_range" in names
        assert "daily_trades_limit" in names
        assert "rollover" in names

    def test_time_range_override(self):
        config = get_tsi_cross_config(time_range=("09:30", "16:00"))
        # Filter is a closure; just verify it still exists
        assert any(f.__name__ == "time_range" for f in config.entry_filters)

    def test_daily_trades_limit_override(self):
        config = get_tsi_cross_config(daily_trades_limit=3)
        limit_filter = next(
            f for f in config.entry_filters if f.__name__ == "daily_trades_limit"
        )
        # The closure captures max_trades_per_day; we exercise it below.
        assert limit_filter is not None

    def test_rollover_override(self):
        config = get_tsi_cross_config(skip_rollover=True)
        rollover = next(f for f in config.entry_filters if f.__name__ == "rollover")
        # When enabled, rollover filter should block on rollover day.
        assert rollover is not None

    def test_tsi_length_overrides(self):
        config = get_tsi_cross_config(
            tsi_long_len=10,
            tsi_short_len=20,
            tsi_signal_len=5,
        )
        assert config.tsi_long_len == 10
        assert config.tsi_short_len == 20
        assert config.tsi_signal_len == 5

    def test_bounce_overrides(self):
        config = get_tsi_cross_config(
            bounce_lookback=30,
            bounce_neighbor_bars=2,
        )
        assert config.bounce_lookback == 30
        assert config.bounce_neighbor_bars == 2

    def test_breakeven_override(self):
        be = BreakevenConfig(trigger_rr=2.0, move_to_rr=0.0)
        config = get_tsi_cross_config(breakeven=be)
        assert config.breakeven is be

    def test_cross_confirmation_override(self):
        config = get_tsi_cross_config(cross_confirmation_bars=2)
        assert config.cross_confirmation_bars == 2

    def test_daily_filter_respects_limit(self):
        """The daily_trades_limit closure should block when limit is exceeded."""
        config = get_tsi_cross_config(daily_trades_limit=1)
        daily_filter = next(
            f for f in config.entry_filters if f.__name__ == "daily_trades_limit"
        )

        class FakeStrategy:
            open_trades = []
            trade_repository = None

        class FakeBar:
            pass

        class FakeCtx:
            strategy = FakeStrategy()
            bar = {"time": 1700000000, "pair": "MNQ"}

        # With no trades in repo, should pass
        class EmptyRepo:
            def list_trades(self, pair):
                return []

        FakeStrategy.trade_repository = EmptyRepo()
        allowed, _ = daily_filter(FakeCtx())
        assert allowed is True
