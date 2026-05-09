"""Application-level ports (abstract interfaces).

These protocols define what the application layer needs from
infrastructure, without coupling to specific implementations.
"""

from typing import Any, Protocol


class EventPublisher(Protocol):
    """Port for publishing events to the outside world.

    Implementations may use SocketIO, EventBus, WebSocket,
    message queues, or any other messaging mechanism.

    This protocol is satisfied by:
    - flask_socketio.SocketIO (has .emit())
    - tests.fakes.DummySocketIO (has .emit())
    - src.infrastructure.event_publisher.DomainEventBusPublisher
    """

    def emit(self, event: str, data: dict[str, Any], **kwargs: Any) -> None:
        """Publish an event with the given name and payload."""
        ...


class Notifier(Protocol):
    """Port for sending notifications (Telegram, email, etc.)."""

    def send(self, message: str) -> None:
        """Send a notification message."""
        ...
