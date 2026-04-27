"""EventBus implementation for decoupled communication.

The EventBus implements the Observer pattern, allowing components to:
1. Publish domain events without knowing who consumes them
2. Subscribe to events without knowing who publishes them

This eliminates direct coupling between:
- Strategy and SocketIO
- TradeManager and SocketIO  
- Controllers and internal state
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Protocol
from collections import defaultdict


class EventType(Enum):
    """Enumeration of all domain event types."""
    # Trade lifecycle events
    TRADE_OPENED = auto()
    TRADE_CLOSED = auto()
    TRADE_UPDATED = auto()  # SL moved, etc.
    
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


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events.
    
    Events are immutable (frozen dataclass) to prevent accidental modification.
    """
    event_type: EventType
    timestamp: datetime = field(default_factory=lambda: datetime.utcnow())
    payload: Dict[str, Any] = field(default_factory=dict)


class EventSubscriber(Protocol):
    """Protocol for event subscribers.
    
    Any class implementing on_event can subscribe to the EventBus.
    """
    def on_event(self, event: DomainEvent) -> None:
        """Handle a domain event."""
        ...


class EventBus:
    """Central event bus for decoupled communication.
    
    Usage:
        # Publishing
        event_bus.publish(DomainEvent(
            event_type=EventType.TRADE_OPENED,
            payload={'trade_id': 'T123', 'entry': 5000.0}
        ))
        
        # Subscribing with function
        event_bus.subscribe(EventType.TRADE_OPENED, my_handler)
        
        # Subscribing with class
        event_bus.add_subscriber(EventType.TRADE_OPENED, MySubscriber())
    """
    
    def __init__(self):
        # Map event types to list of handler functions
        self._handlers: Dict[EventType, List[Callable[[DomainEvent], None]]] = defaultdict(list)
        # Map event types to list of subscriber objects
        self._subscribers: Dict[EventType, List[EventSubscriber]] = defaultdict(list)
    
    def subscribe(
        self,
        event_type: EventType,
        handler: Callable[[DomainEvent], None]
    ) -> None:
        """Subscribe a function handler to an event type.
        
        Args:
            event_type: Type of event to subscribe to
            handler: Function that will be called when event occurs
        """
        self._handlers[event_type].append(handler)
    
    def unsubscribe(
        self,
        event_type: EventType,
        handler: Callable[[DomainEvent], None]
    ) -> None:
        """Unsubscribe a function handler from an event type.
        
        Args:
            event_type: Type of event to unsubscribe from
            handler: Function to remove
        """
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)
    
    def add_subscriber(
        self,
        event_type: EventType,
        subscriber: EventSubscriber
    ) -> None:
        """Add a subscriber object to an event type.
        
        Args:
            event_type: Type of event to subscribe to
            subscriber: Object implementing EventSubscriber protocol
        """
        self._subscribers[event_type].append(subscriber)
    
    def remove_subscriber(
        self,
        event_type: EventType,
        subscriber: EventSubscriber
    ) -> None:
        """Remove a subscriber object from an event type.
        
        Args:
            event_type: Type of event to unsubscribe from
            subscriber: Subscriber object to remove
        """
        if subscriber in self._subscribers[event_type]:
            self._subscribers[event_type].remove(subscriber)
    
    def publish(self, event: DomainEvent) -> None:
        """Publish an event to all subscribers.
        
        Args:
            event: DomainEvent to publish
        """
        event_type = event.event_type
        
        # Call function handlers
        for handler in self._handlers[event_type]:
            try:
                handler(event)
            except Exception as e:
                # Log error but don't stop other handlers
                print(f"[EventBus] Handler error for {event_type}: {e}")
        
        # Call subscriber objects
        for subscriber in self._subscribers[event_type]:
            try:
                subscriber.on_event(event)
            except Exception as e:
                # Log error but don't stop other subscribers
                print(f"[EventBus] Subscriber error for {event_type}: {e}")
    
    def publish_typed(
        self,
        event_type: EventType,
        payload: Dict[str, Any]
    ) -> None:
        """Convenience method to create and publish an event.
        
        Args:
            event_type: Type of event
            payload: Event data dictionary
        """
        self.publish(DomainEvent(
            event_type=event_type,
            payload=payload
        ))
    
    def clear(self) -> None:
        """Clear all handlers and subscribers. Useful for testing."""
        self._handlers.clear()
        self._subscribers.clear()


class SocketIOBridge:
    """Bridge that forwards domain events to SocketIO.
    
    This is the ONLY place where EventBus events are translated to SocketIO.
    It decouples all business logic from SocketIO emissions.
    
    Usage:
        socketio_bridge = SocketIOBridge(socketio, event_bus)
        socketio_bridge.start()  # Registers all listeners
    """
    
    def __init__(
        self,
        socketio: Any,  # flask_socketio.SocketIO
        event_bus: EventBus,
    ):
        self.socketio = socketio
        self.event_bus = event_bus
    
    def start(self) -> None:
        """Start forwarding events to SocketIO.
        
        Registers all event type handlers.
        """
        self.event_bus.subscribe(EventType.TRADE_OPENED, self._on_trade_opened)
        self.event_bus.subscribe(EventType.TRADE_CLOSED, self._on_trade_closed)
        self.event_bus.subscribe(EventType.TRADE_UPDATED, self._on_trade_updated)
        self.event_bus.subscribe(EventType.LINE_ADDED, self._on_line_added)
        self.event_bus.subscribe(EventType.LINE_REMOVED, self._on_line_removed)
        self.event_bus.subscribe(EventType.LINE_UPDATED, self._on_line_updated)
        self.event_bus.subscribe(EventType.STREAM_STARTED, self._on_stream_started)
        self.event_bus.subscribe(EventType.STREAM_PAUSED, self._on_stream_paused)
        self.event_bus.subscribe(EventType.STREAM_ENDED, self._on_stream_ended)
    
    def stop(self) -> None:
        """Stop forwarding events."""
        self.event_bus.unsubscribe(EventType.TRADE_OPENED, self._on_trade_opened)
        self.event_bus.unsubscribe(EventType.TRADE_CLOSED, self._on_trade_closed)
        self.event_bus.unsubscribe(EventType.TRADE_UPDATED, self._on_trade_updated)
        self.event_bus.unsubscribe(EventType.LINE_ADDED, self._on_line_added)
        self.event_bus.unsubscribe(EventType.LINE_REMOVED, self._on_line_removed)
        self.event_bus.unsubscribe(EventType.LINE_UPDATED, self._on_line_updated)
        self.event_bus.unsubscribe(EventType.STREAM_STARTED, self._on_stream_started)
        self.event_bus.unsubscribe(EventType.STREAM_PAUSED, self._on_stream_paused)
        self.event_bus.unsubscribe(EventType.STREAM_ENDED, self._on_stream_ended)
    
    def _on_trade_opened(self, event: DomainEvent) -> None:
        """Forward trade open event."""
        self.socketio.emit('trade_open', event.payload)
    
    def _on_trade_closed(self, event: DomainEvent) -> None:
        """Forward trade close event."""
        self.socketio.emit('trade_close', event.payload)
    
    def _on_trade_updated(self, event: DomainEvent) -> None:
        """Forward trade update event (e.g., SL moved)."""
        self.socketio.emit('trade_update', event.payload)
    
    def _on_line_added(self, event: DomainEvent) -> None:
        """Forward line added event."""
        self.socketio.emit('line_added', event.payload)
    
    def _on_line_removed(self, event: DomainEvent) -> None:
        """Forward line removed event."""
        self.socketio.emit('line_removed', event.payload)
    
    def _on_line_updated(self, event: DomainEvent) -> None:
        """Forward line updated event."""
        self.socketio.emit('line_updated', event.payload)
    
    def _on_stream_started(self, event: DomainEvent) -> None:
        """Forward stream started event."""
        self.socketio.emit('stream_status', {'playing': True})
    
    def _on_stream_paused(self, event: DomainEvent) -> None:
        """Forward stream paused event."""
        self.socketio.emit('stream_status', {'playing': False})
    
    def _on_stream_ended(self, event: DomainEvent) -> None:
        """Forward stream ended event."""
        reason = event.payload.get('reason', 'eof')
        self.socketio.emit('stream_end', {'reason': reason})


# Global event bus singleton for convenience
# Use this in most cases, or create separate instances for testing
_default_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the default global event bus.
    
    Returns:
        The default EventBus instance (creates one if needed)
    """
    global _default_event_bus
    if _default_event_bus is None:
        _default_event_bus = EventBus()
    return _default_event_bus


def reset_event_bus() -> None:
    """Reset the global event bus. Useful for testing."""
    global _default_event_bus
    _default_event_bus = EventBus()
