"""Application-level ports (abstract interfaces).

These protocols define what the application layer needs from
infrastructure, without coupling to specific implementations.
"""

from typing import Any, Protocol


class PlatformLifecycleService(Protocol):
    """Port for platform-specific streaming lifecycle behavior.

    Implementations handle pre-flight validation (e.g. account checks)
    and optional auto-launch of the external trading platform.
    """

    def validate_before_start(self, data_source) -> tuple[bool, str | None]:
        """Return (ok, error_message_or_none)."""
        ...

    def maybe_launch_after_delay(self, data_source) -> None:
        """Called in a background thread after the gateway starts.

        Should wait a few seconds for the platform to connect on its own,
        then attempt to launch it if it hasn't.
        """
        ...

    def has_accounts_configured(self) -> bool:
        """Return True if the platform has accounts set up."""
        ...


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
