# src/events package
"""Event-driven architecture components.

This package provides the EventBus for decoupled communication between components.
Instead of direct SocketIO emissions, components publish domain events that can be
consumed by multiple subscribers.
"""

from .event_bus import DomainEvent, EventBus, EventSubscriber, EventType
from .trade_events import (
    LineAddedEvent,
    LineRemovedEvent,
    LineUpdatedEvent,
    TradeClosedEvent,
    TradeOpenedEvent,
    TradeUpdatedEvent,
)

__all__ = [
    # Core
    "EventBus",
    "EventType",
    "DomainEvent",
    "EventSubscriber",
    # Trade events
    "TradeOpenedEvent",
    "TradeClosedEvent",
    "TradeUpdatedEvent",
    # Line events
    "LineAddedEvent",
    "LineRemovedEvent",
    "LineUpdatedEvent",
]
