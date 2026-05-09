"""Infrastructure event publisher implementations.

These adapters wrap concrete messaging mechanisms (SocketIO, EventBus)
to satisfy the application-level EventPublisher port.
"""

from typing import Any

from src.domain.events import DomainEvent, EventType
from src.events.event_bus import EventBus


class SocketIOEventPublisher:
    """Adapter that delegates emit() calls directly to SocketIO.

    This is a thin wrapper used during transition from direct SocketIO
coupling to full domain-event architecture.
    """

    def __init__(self, socketio: Any):
        self._socketio = socketio

    def emit(self, event: str, data: dict[str, Any], **kwargs: Any) -> None:
        self._socketio.emit(event, data, **kwargs)


class DomainEventBusPublisher:
    """Adapter that translates emit() calls to domain events on the EventBus.

    Maps common event names to DomainEvent types so that strategies
    and trade managers can publish without knowing about SocketIO.
    """

    _EVENT_MAP: dict[str, EventType] = {
        "trade_open": EventType.TRADE_OPENED,
        "trade_close": EventType.TRADE_CLOSED,
        "trade_update": EventType.TRADE_UPDATED,
        "trade_entry_update": EventType.TRADE_UPDATED,
        "line_added": EventType.LINE_ADDED,
        "line_removed": EventType.LINE_REMOVED,
        "line_updated": EventType.LINE_UPDATED,
        "stream_status": EventType.STREAM_STARTED,
        "stream_end": EventType.STREAM_ENDED,
        "indicator_update": EventType.INDICATOR_UPDATE,
    }

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus

    def emit(self, event: str, data: dict[str, Any], **kwargs: Any) -> None:
        event_type = self._EVENT_MAP.get(event)
        if event_type is None:
            # Unknown event — forward as-is via a generic handler or log
            return
        self._event_bus.publish(
            DomainEvent(event_type=event_type, payload=dict(data))
        )


class CompositeEventPublisher:
    """Publishes to multiple backends simultaneously.

    Useful during migration: publish to both EventBus AND SocketIO
    until all consumers have moved to event-driven subscriptions.
    """

    def __init__(self, *publishers: Any):
        self._publishers = publishers

    def emit(self, event: str, data: dict[str, Any], **kwargs: Any) -> None:
        for pub in self._publishers:
            try:
                pub.emit(event, data, **kwargs)
            except Exception:
                # Don't let one publisher failure break the others
                pass
