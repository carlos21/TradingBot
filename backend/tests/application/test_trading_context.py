"""Unit tests for trading context implementations."""

from __future__ import annotations

import pytest

from src.application.live_readiness.trading_context import (
    AlwaysEnabledTradingContext,
    ReadinessTradingContext,
)
from src.domain.readiness import ReadinessStateMachine


class TestAlwaysEnabledTradingContext:
    def test_trading_always_enabled(self) -> None:
        ctx = AlwaysEnabledTradingContext()
        assert ctx.is_trading_enabled() is True

    def test_never_warmup(self) -> None:
        ctx = AlwaysEnabledTradingContext()
        assert ctx.is_warmup() is False


class TestReadinessTradingContext:
    def test_trading_disabled_initially(self) -> None:
        sm = ReadinessStateMachine()
        ctx = ReadinessTradingContext(sm)
        assert ctx.is_trading_enabled() is False
        assert ctx.is_warmup() is False

    def test_trading_enabled_in_ready(self) -> None:
        sm = ReadinessStateMachine()
        sm.connect()
        sm.history_loaded()
        sm.warmup_complete()
        ctx = ReadinessTradingContext(sm)
        assert ctx.is_trading_enabled() is True
        assert ctx.is_warmup() is False

    def test_trading_enabled_in_live(self) -> None:
        sm = ReadinessStateMachine()
        sm.connect()
        sm.history_loaded()
        sm.warmup_complete()
        sm.live_bar_received()
        ctx = ReadinessTradingContext(sm)
        assert ctx.is_trading_enabled() is True
        assert ctx.is_warmup() is False

    def test_warmup_only_in_warming_up(self) -> None:
        sm = ReadinessStateMachine()
        sm.connect()
        sm.history_loaded()
        ctx = ReadinessTradingContext(sm)
        assert ctx.is_warmup() is True
        assert ctx.is_trading_enabled() is False

    @pytest.mark.parametrize(
        "transition_method",
        [
            "connect",
            "start_refresh",
            "history_empty",
            "history_retry_scheduled",
            "degrade",
            "disconnect",
        ],
    )
    def test_not_ready_in_other_states(self, transition_method: str) -> None:
        sm = ReadinessStateMachine()
        sm.connect()
        transition = {
            "connect": sm.connect,
            "start_refresh": sm.start_refresh,
            "history_empty": sm.history_empty,
            "history_retry_scheduled": sm.history_retry_scheduled,
            "degrade": sm.degrade,
            "disconnect": sm.disconnect,
        }[transition_method]
        transition("test") if transition_method == "degrade" else transition()
        ctx = ReadinessTradingContext(sm)
        assert ctx.is_trading_enabled() is False
        assert ctx.is_warmup() is False
