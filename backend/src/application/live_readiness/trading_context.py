"""Trading context implementations used by the strategy."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.readiness import ReadinessStateMachine

from src.domain.readiness.protocols import IExecutionContext


class AlwaysEnabledTradingContext(IExecutionContext):
    """
    Context for backtest/replay and unit tests.

    Trading is always enabled and the system is never considered to be in
    warm-up mode, so the strategy behaves exactly as it did before the
    readiness refactor.
    """

    def is_trading_enabled(self) -> bool:
        return True

    def is_warmup(self) -> bool:
        return False


class ReadinessTradingContext(IExecutionContext):
    """
    Context backed by the live readiness state machine.

    Trading is enabled only when the state machine is READY or LIVE.
    Warm-up mode is active while the state machine is WARMING_UP.
    """

    def __init__(self, state_machine: ReadinessStateMachine) -> None:
        self._state_machine = state_machine

    def is_trading_enabled(self) -> bool:
        return self._state_machine.is_ready()

    def is_warmup(self) -> bool:
        from src.domain.readiness.readiness_state import ReadinessState
        return self._state_machine.state == ReadinessState.WARMING_UP
