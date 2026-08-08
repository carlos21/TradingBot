"""Tests for src.strategies.liquidity_v2.config."""

from dataclasses import fields

from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers


class TestStrategyNumbers:

    def test_defaults(self):
        sn = StrategyNumbers(min_stop_loss=10.0, max_bounce=5.0, extra_sl_space=0.0)
        assert sn.min_stop_loss == 10.0
        assert sn.max_bounce == 5.0
        assert sn.extra_sl_space == 0.0
        assert sn.fixed_stop_loss is None
        assert sn.max_stop_loss is None
        assert sn.sl_levels is None
        assert sn.sl_level_tolerance == 5.0
        assert sn.min_cross_depth == 0.0
        assert sn.rr_ratio == 5.0
        assert sn.point_value == 2.0
        assert sn.account_balance == 50000.0
        assert sn.risk_per_trade is None
        assert sn.risk_pct_per_trade is None
        assert sn.max_entry_distance is None
        assert sn.account_configs == []

    def test_all_fields_set(self):
        sn = StrategyNumbers(
            min_stop_loss=5.0,
            max_bounce=10.0,
            extra_sl_space=2.0,
            fixed_stop_loss=20.0,
            max_stop_loss=50.0,
            sl_levels=[10.0, 20.0],
            sl_level_tolerance=3.0,
            min_cross_depth=4.0,
            rr_ratio=3.3,
            point_value=5.0,
            account_balance=100000.0,
            risk_per_trade=100.0,
            risk_pct_per_trade=1.0,
            max_entry_distance=15.0,
            account_configs=[{"name": "A1"}],
        )
        assert sn.min_stop_loss == 5.0
        assert sn.sl_levels == [10.0, 20.0]
        assert sn.account_configs == [{"name": "A1"}]

    def test_field_count(self):
        # Guard against silently added fields
        assert len(fields(StrategyNumbers)) == 19


class TestCandleConfig:

    def test_defaults(self):
        cc = CandleConfig()
        assert cc.small_body_max_ratio == 0.25
        assert cc.wick_min_ratio == 0.60
        assert cc.big_body_min_ratio == 0.40
        assert cc.hammer_body_max_ratio == 0.30
        assert cc.hammer_nose_max_ratio == 0.25

    def test_custom_values(self):
        cc = CandleConfig(
            small_body_max_ratio=0.3,
            wick_min_ratio=0.5,
            big_body_min_ratio=0.35,
            hammer_body_max_ratio=0.25,
            hammer_nose_max_ratio=0.2,
        )
        assert cc.small_body_max_ratio == 0.3
        assert cc.wick_min_ratio == 0.5

    def test_field_count(self):
        assert len(fields(CandleConfig)) == 5
