"""Tests for src.strategies.liquidity_v2.constants."""

from src.strategies.liquidity_v2.base_strategy import LineRemovalMode, StrategyOptions
from src.strategies.liquidity_v2.constants import (
    DEFAULT_LINE_REMOVAL_MODE,
    DEFAULT_MAX_REENTRY_ATTEMPTS,
    DEFAULT_REENTRY_AFTER_SL,
    DEFAULT_REENTRY_ONLY,
    DEFAULT_REENTRY_THRESHOLD,
    DEFAULT_STRATEGY_OPTIONS,
)


class TestLiquidityV2Constants:

    def test_default_line_removal_mode(self):
        assert DEFAULT_LINE_REMOVAL_MODE == LineRemovalMode.ON_EVALUATE

    def test_default_reentry_flags(self):
        assert DEFAULT_REENTRY_AFTER_SL is False
        assert DEFAULT_REENTRY_ONLY is False
        assert DEFAULT_REENTRY_THRESHOLD == 60.0
        assert DEFAULT_MAX_REENTRY_ATTEMPTS == 1

    def test_default_strategy_options_type(self):
        assert isinstance(DEFAULT_STRATEGY_OPTIONS, StrategyOptions)

    def test_default_strategy_options_values(self):
        opts = DEFAULT_STRATEGY_OPTIONS
        assert opts.line_removal_mode == LineRemovalMode.ON_EVALUATE
        assert opts.entry_filters is None
        assert opts.triggers is None
        assert opts.breakeven is None
        assert opts.reentry_breakeven is None
        assert opts.reentry_after_sl is False
        assert opts.reentry_threshold == 60.0
        assert opts.reentry_only is False
        assert opts.max_reentry_attempts == 1

    def test_default_strategy_options_is_replaceable(self):
        """DEFAULT_STRATEGY_OPTIONS must be a real instance for dataclasses.replace()."""
        replaced = StrategyOptions(
            line_removal_mode=LineRemovalMode.ON_ENTER,
            entry_filters=None,
            triggers=None,
            breakeven=None,
            reentry_breakeven=None,
            reentry_after_sl=True,
            reentry_threshold=30.0,
            reentry_only=False,
            max_reentry_attempts=2,
        )
        assert replaced.line_removal_mode == LineRemovalMode.ON_ENTER
        assert replaced.reentry_after_sl is True
