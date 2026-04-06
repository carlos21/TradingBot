# src/events package
"""Event-driven architecture components.

This package provides the EventBus for decoupled communication between components.
Instead of direct SocketIO emissions, components publish domain events that can be
consumed by multiple subscribers.
"""

from .event_bus import EventBus, EventType, DomainEvent, EventSubscriber
from .trade_events import (
    TradeOpenedEvent,
    TradeClosedEvent,
    TradeUpdatedEvent,
    LineAddedEvent,
    LineRemovedEvent,
    LineUpdatedEvent,
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
