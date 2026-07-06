"""Domain events for the trading bot.

Domain events represent significant occurrences in the business domain.
They are immutable and contain all data needed by subscribers.

These events are published by the domain/application layers and consumed
by infrastructure adapters (e.g., SocketIOBridge, loggers, analytics).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any


class EventType(Enum):
    """Enumeration of all domain event types."""
    # Trade lifecycle events
    TRADE_OPENED = auto()
    TRADE_CLOSED = auto()
    TRADE_UPDATED = auto()  # SL moved, etc.
    TRADE_ENTRY_UPDATED = auto()  # broker entry fill price applied

    # Line events
    LINE_ADDED = auto()
    LINE_REMOVED = auto()
    LINE_UPDATED = auto()

    # Stream events
    STREAM_STARTED = auto()
    STREAM_PAUSED = auto()
    STREAM_ENDED = auto()

    # Strategy events
    ENTRY_SIGNAL = auto()
    FILTER_BLOCKED = auto()
    INDICATOR_UPDATE = auto()

    # Readiness / lifecycle events
    READINESS_CHANGED = auto()

    # Balance / Account events
    BALANCE_UPDATED = auto()


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events.

    Events are immutable (frozen dataclass) to prevent accidental modification.
    """
    event_type: EventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = field(default_factory=dict)
