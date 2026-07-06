"""Tests for src/prod_config.py — factory functions for production configuration."""

from src.strategies.liquidity_v2.base_strategy import LineRemovalMode, StrategyOptions
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.prod_config import (
    get_prod_candle_config,
    get_prod_strategy_numbers,
    get_prod_strategy_options,
)


class TestGetProdStrategyNumbers:

    def test_returns_strategy_numbers(self):
        sn = get_prod_strategy_numbers(rr_ratio=5.0)
        assert isinstance(sn, StrategyNumbers)

    def test_rr_ratio_required(self):
        sn = get_prod_strategy_numbers(rr_ratio=3.3)
        assert sn.rr_ratio == 3.3

    def test_custom_rr_ratio(self):
        sn = get_prod_strategy_numbers(rr_ratio=5.0)
        assert sn.rr_ratio == 5.0

    def test_sl_levels_sorted(self):
        sn = get_prod_strategy_numbers(rr_ratio=5.0)
        assert sn.sl_levels == sorted(sn.sl_levels)

    def test_min_stop_loss_positive(self):
        sn = get_prod_strategy_numbers(rr_ratio=5.0)
        assert sn.min_stop_loss > 0

    def test_max_bounce_positive(self):
        sn = get_prod_strategy_numbers(rr_ratio=5.0)
        assert sn.max_bounce > 0


class TestGetProdCandleConfig:

    def test_returns_candle_config(self):
        cc = get_prod_candle_config()
        assert isinstance(cc, CandleConfig)

    def test_has_positive_ratios(self):
        cc = get_prod_candle_config()
        assert cc.small_body_max_ratio > 0
        assert cc.wick_min_ratio > 0


class TestGetProdStrategyOptions:

    def test_returns_strategy_options(self):
        opts = get_prod_strategy_options(max_bounce=90.0, min_cross_depth=5.0, skip_rollover_days=False, reentry_only=False, line_removal_mode=LineRemovalMode.ON_EVALUATE, max_reentry_attempts=1)
        assert isinstance(opts, StrategyOptions)

    def test_has_filters(self):
        opts = get_prod_strategy_options(max_bounce=90.0, min_cross_depth=5.0, skip_rollover_days=False, reentry_only=False, line_removal_mode=LineRemovalMode.ON_EVALUATE, max_reentry_attempts=1)
        assert len(opts.entry_filters) > 0

    def test_has_triggers(self):
        opts = get_prod_strategy_options(max_bounce=90.0, min_cross_depth=5.0, skip_rollover_days=False, reentry_only=False, line_removal_mode=LineRemovalMode.ON_EVALUATE, max_reentry_attempts=1)
        assert len(opts.triggers) > 0

    def test_reentry_after_sl_enabled(self):
        opts = get_prod_strategy_options(max_bounce=90.0, min_cross_depth=5.0, skip_rollover_days=False, reentry_only=False, line_removal_mode=LineRemovalMode.ON_EVALUATE, max_reentry_attempts=1)
        assert opts.reentry_after_sl is True

    def test_reentry_breakeven_configured(self):
        opts = get_prod_strategy_options(max_bounce=90.0, min_cross_depth=5.0, skip_rollover_days=False, reentry_only=False, line_removal_mode=LineRemovalMode.ON_EVALUATE, max_reentry_attempts=1)
        assert opts.reentry_breakeven is not None
        assert opts.reentry_breakeven.trigger_rr == 2.0
