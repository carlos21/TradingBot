"""Strategy protocols for type-safe dependency inversion.

These protocols declare the contract between app_factory, controllers,
and strategy implementations. They enable clean architecture by allowing
 callers to depend on abstractions rather than concrete classes.
"""

from __future__ import annotations

from threading import RLock
from typing import Any, Protocol

from src.events.event_bus import EventSubscriber


class LiquidityStrategy(EventSubscriber, Protocol):
    """Protocol for liquidity-based strategies.

    Any strategy used by app_factory and the bar-processing pipeline must
    satisfy this interface. Implementations are structurally checked at
    type-check time (mypy/pyright) and runtime (duck typing).
    """

    # ------------------------------------------------------------------
    # State attributes
    # ------------------------------------------------------------------
    is_warmup: bool
    options: Any
    strategy_lines: dict[str, Any]
    lock: RLock
    warmup_crossed_lines: set[Any]

    # ------------------------------------------------------------------
    # EventSubscriber (inherited from EventSubscriber protocol)
    # ------------------------------------------------------------------
    # def on_event(self, event: DomainEvent) -> None: ...

    # ------------------------------------------------------------------
    # Bar processing
    # ------------------------------------------------------------------
    def on_raw_bar(self, bar: dict[str, Any]) -> None:
        """Process a raw bar (1s or tick granularity)."""
        ...

    def check_breakeven(self, bar: dict[str, Any]) -> None:
        """Evaluate breakeven conditions for open trades."""
        ...

    # ------------------------------------------------------------------
    # Line management
    # ------------------------------------------------------------------
    def add_strategy_line(
        self,
        id: Any,
        level: float,
        creation_timestamp: float = 0.0,
    ) -> None:
        """Add a strategy line at the given price level."""
        ...

    def remove_strategy_line(self, id: Any) -> None:
        """Remove a strategy line by id."""
        ...

    def update_strategy_line(self, id: Any, level: float) -> None:
        """Update an existing strategy line's price level."""
        ...

    # ------------------------------------------------------------------
    # Lifecycle / restore
    # ------------------------------------------------------------------
    def restore_open_trades(self) -> None:
        """Sync open trades from TradeManager into strategy's in-memory list."""
        ...

    def restore_trigger_states(self, pair: str) -> None:
        """Overlay persisted trigger states onto bootstrapped lines."""
        ...

    def restore_reentry_opportunities(
        self, pair: str, reference_time: Any = None
    ) -> None:
        """Rebuild pending re-entry opportunities from recently SL'd trades."""
        ...
