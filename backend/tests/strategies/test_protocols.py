"""Tests for src.strategies.protocols."""

import inspect

from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
from src.strategies.protocols import LiquidityStrategy
from src.strategies.tsi_cross.strategy import TsiCrossStrategy


class TestLiquidityStrategyProtocol:

    def test_protocol_attributes_declared(self):
        """LiquidityStrategy protocol must declare the expected public surface."""
        annotations = getattr(LiquidityStrategy, "__annotations__", {})
        assert "is_warmup" in annotations
        assert "options" in annotations
        assert "strategy_lines" in annotations
        assert "lock" in annotations
        assert "warmup_crossed_lines" in annotations

    def test_protocol_methods_declared(self):
        members = dict(inspect.getmembers(LiquidityStrategy))
        assert "on_raw_bar" in members
        assert "check_breakeven" in members
        assert "add_strategy_line" in members
        assert "remove_strategy_line" in members
        assert "update_strategy_line" in members
        assert "restore_open_trades" in members
        assert "restore_trigger_states" in members
        assert "restore_reentry_opportunities" in members

    def test_liquidity_strategy_v2_implements_protocol(self):
        v2_methods = {
            "on_raw_bar", "check_breakeven", "add_strategy_line",
            "remove_strategy_line", "update_strategy_line",
            "restore_open_trades", "restore_trigger_states",
            "restore_reentry_opportunities",
        }
        for method in v2_methods:
            assert hasattr(LiquidityStrategyV2, method), f"LiquidityStrategyV2 missing {method}"

    def test_tsi_cross_strategy_has_core_methods(self):
        """TsiCrossStrategy intentionally does not implement the full line API,
        but it must satisfy EventSubscriber via on_event and bar processing via
        on_raw_bar."""
        assert hasattr(TsiCrossStrategy, "on_raw_bar")
        assert hasattr(TsiCrossStrategy, "on_event")
        assert hasattr(TsiCrossStrategy, "is_warmup")

    def test_protocol_is_subclass_of_event_subscriber(self):
        # LiquidityStrategy inherits from EventSubscriber (Protocol)
        from src.events.event_bus import EventSubscriber
        assert EventSubscriber in LiquidityStrategy.__bases__
