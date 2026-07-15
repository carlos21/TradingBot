"""Tests for src.domain.readiness.protocols."""
from typing import Any

from src.domain.readiness.protocols import (
    IExecutionContext,
    IReadinessObserver,
    ITradingGate,
    IWarmupPolicy,
)


class FakeTradingGate(ITradingGate):
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def is_trading_enabled(self) -> bool:
        return self.enabled


class FakeExecutionContext(IExecutionContext):
    def __init__(self, enabled: bool = True, warmup: bool = False):
        self.enabled = enabled
        self.warmup = warmup

    def is_trading_enabled(self) -> bool:
        return self.enabled

    def is_warmup(self) -> bool:
        return self.warmup


class FakeWarmupPolicy(IWarmupPolicy):
    def __init__(self, warm: bool = True):
        self.warm = warm

    def is_warm(self, strategy) -> bool:
        return self.warm


class FakeReadinessObserver(IReadinessObserver):
    def __init__(self):
        self.changes: list[tuple[Any, Any, str]] = []

    def on_readiness_changed(
        self,
        state: Any,
        previous_state: Any,
        reason: str,
    ) -> None:
        self.changes.append((state, previous_state, reason))


class TestReadinessProtocols:
    def test_trading_gate(self):
        gate = FakeTradingGate(enabled=False)
        assert gate.is_trading_enabled() is False

    def test_execution_context(self):
        ctx = FakeExecutionContext(enabled=True, warmup=True)
        assert ctx.is_trading_enabled() is True
        assert ctx.is_warmup() is True

    def test_warmup_policy(self):
        policy = FakeWarmupPolicy(warm=False)
        assert policy.is_warm(None) is False

    def test_readiness_observer(self):
        observer = FakeReadinessObserver()
        observer.on_readiness_changed("ready", "warming_up", "history complete")
        assert observer.changes == [("ready", "warming_up", "history complete")]
