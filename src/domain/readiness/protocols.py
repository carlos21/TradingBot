"""Ports/abstractions for readiness-related collaborators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2


class ITradingGate(Protocol):
    """Decides whether the strategy is allowed to open trades right now."""

    def is_trading_enabled(self) -> bool:
        """Return True when entries/exits that hit the broker may fire."""
        ...


class IExecutionContext(ITradingGate, Protocol):
    """Execution context exposed to the strategy."""

    def is_warmup(self) -> bool:
        """Return True when the strategy is replaying historical bars."""
        ...


class IWarmupPolicy(Protocol):
    """Decides whether the strategy's indicators have enough history."""

    def is_warm(self, strategy: LiquidityStrategyV2) -> bool:
        """Return True when all required indicator histories are warm."""
        ...


class IReadinessObserver(Protocol):
    """Subscriber for readiness state changes."""

    def on_readiness_changed(
        self,
        state: Any,
        previous_state: Any,
        reason: str,
    ) -> None:
        """Called whenever the readiness state changes."""
        ...
