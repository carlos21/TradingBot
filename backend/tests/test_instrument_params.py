"""Tests for the hardcoded instrument catalog and per-symbol prod config."""
import pytest

from src.strategies.liquidity_v2.instrument_params import (
    DEFAULT_SYMBOL,
    INSTRUMENT_PARAMS,
    HardcodedInstrumentCatalog,
    get_instrument_params,
    supported_symbols,
)
from src.strategies.liquidity_v2.prod_config import (
    get_prod_strategy_numbers,
    get_prod_strategy_options,
)
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS


class TestCatalog:
    def test_supported_symbols(self):
        assert supported_symbols() == ["MNQ", "MES"]

    def test_mnq_values_match_prod(self):
        p = INSTRUMENT_PARAMS["MNQ"]
        assert p.point_value == 2.0
        assert p.min_stop_loss == 10.0
        assert p.max_bounce == 90.0
        assert p.sl_levels == [15.0, 20.0, 30.0, 40.0]
        assert p.sl_level_tolerance == 3.0
        assert p.min_cross_depth == 5.0
        assert p.max_entry_distance == 50.0
        assert p.reentry_threshold == 90.0
        assert p.fast_threshold == 28.0
        assert p.slow_threshold == 15.0
        assert p.post_cross1_max_dist == 80.0
        assert p.be_threshold_points == 2.0
        assert p.sl_tp_tolerance == 0.5
        assert p.max_open_trades == 2

    def test_windows_carry_per_window_open_trades_limit(self):
        """Catalog: global cap is 2 (cross-window concurrency); each window
        caps its own concurrent open legs at 1 and initial entries at 1."""
        for p in INSTRUMENT_PARAMS.values():
            assert p.max_open_trades == 2
            for w in p.trading_windows:
                assert w.max_open_trades == 1
                assert w.max_trades == 1

    def test_unknown_symbol_falls_back_to_default(self):
        assert get_instrument_params("ZZZ") is INSTRUMENT_PARAMS[DEFAULT_SYMBOL]
        assert get_instrument_params(None) is INSTRUMENT_PARAMS[DEFAULT_SYMBOL]

    def test_hardcoded_catalog_protocol(self):
        catalog = HardcodedInstrumentCatalog()
        defaults = catalog.get_defaults()
        assert [i.symbol for i in defaults] == ["MNQ", "MES"]
        assert defaults[0].full_name == "MNQ 09-26"
        assert defaults[1].point_value == 5.0
        assert catalog.default_symbol() == "MNQ"


class TestPerSymbolProdConfig:
    def test_numbers_default_symbol_is_mnq(self):
        numbers = get_prod_strategy_numbers(rr_ratio=5.0)
        assert numbers.point_value == 2.0
        assert numbers.sl_levels == [15.0, 20.0, 30.0, 40.0]
        assert numbers.be_threshold_points == 2.0
        assert numbers.sl_tp_tolerance == 0.5

    def test_numbers_mes_uses_mes_values(self):
        numbers = get_prod_strategy_numbers(rr_ratio=5.0, symbol="MES")
        assert numbers.point_value == 5.0
        assert numbers.sl_levels == [5.0, 7.0, 10.0, 14.0]
        assert numbers.min_cross_depth == 1.5
        assert numbers.max_entry_distance == 15.0
        assert numbers.be_threshold_points == 0.75
        assert numbers.sl_tp_tolerance == 0.25

    def test_options_per_symbol(self):
        mnq = get_prod_strategy_numbers(rr_ratio=5.0, symbol="MNQ")
        mes = get_prod_strategy_numbers(rr_ratio=5.0, symbol="MES")
        common = dict(
            skip_rollover_days=False,
            reentry_only=False,
            line_removal_mode=DEFAULT_STRATEGY_OPTIONS.line_removal_mode,
            max_reentry_attempts=1,
        )
        mnq_options = get_prod_strategy_options(mnq.max_bounce, mnq.min_cross_depth, symbol="MNQ", **common)
        mes_options = get_prod_strategy_options(mes.max_bounce, mes.min_cross_depth, symbol="MES", **common)
        assert mnq_options.reentry_threshold == 90.0
        assert mes_options.reentry_threshold == 30.0

    def test_options_default_windows_come_from_catalog(self):
        numbers = get_prod_strategy_numbers(rr_ratio=5.0)
        options = get_prod_strategy_options(
            numbers.max_bounce,
            numbers.min_cross_depth,
            skip_rollover_days=False,
            reentry_only=False,
            line_removal_mode=DEFAULT_STRATEGY_OPTIONS.line_removal_mode,
            max_reentry_attempts=1,
        )
        # The trading_windows filter is installed by default (catalog windows).
        filter_names = [f.__name__ for f in options.entry_filters]
        assert "trading_windows" in filter_names

    def test_open_trades_limit_precedes_trading_windows(self):
        numbers = get_prod_strategy_numbers(rr_ratio=5.0)
        options = get_prod_strategy_options(
            numbers.max_bounce,
            numbers.min_cross_depth,
            skip_rollover_days=False,
            reentry_only=False,
            line_removal_mode=DEFAULT_STRATEGY_OPTIONS.line_removal_mode,
            max_reentry_attempts=1,
        )
        filter_names = [f.__name__ for f in options.entry_filters]
        assert "open_trades_limit" in filter_names
        assert filter_names.index("open_trades_limit") < filter_names.index("trading_windows")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
