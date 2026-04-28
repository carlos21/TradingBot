"""Tests for src/strategies/strategy_config.py — StrategyNumbers and CandleConfig dataclasses."""

from src.strategies.strategy_config import CandleConfig, StrategyNumbers


class TestStrategyNumbers:

    def test_defaults(self):
        sn = StrategyNumbers(min_stop_loss=10, max_bounce=50, extra_sl_space=2)
        assert sn.fixed_stop_loss is None
        assert sn.max_stop_loss is None
        assert sn.sl_levels is None
        assert sn.sl_level_tolerance == 5.0
        assert sn.min_cross_depth == 0.0
        assert sn.rr_ratio == 5.0

    def test_all_fields(self):
        sn = StrategyNumbers(
            min_stop_loss=10, max_bounce=50, extra_sl_space=2,
            fixed_stop_loss=20, max_stop_loss=40,
            sl_levels=[15, 20, 30], sl_level_tolerance=3,
            min_cross_depth=5.0, rr_ratio=3.3,
        )
        assert sn.sl_levels == [15, 20, 30]
        assert sn.rr_ratio == 3.3


class TestCandleConfig:

    def test_defaults(self):
        cc = CandleConfig()
        assert cc.small_body_max_ratio == 0.25
        assert cc.wick_min_ratio == 0.60
        assert cc.big_body_min_ratio == 0.40
        assert cc.hammer_body_max_ratio == 0.30
        assert cc.hammer_nose_max_ratio == 0.25

    def test_custom_values(self):
        cc = CandleConfig(small_body_max_ratio=0.3, wick_min_ratio=0.5)
        assert cc.small_body_max_ratio == 0.3
        assert cc.wick_min_ratio == 0.5
