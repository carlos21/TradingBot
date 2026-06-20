"""Tests for src/infrastructure/event_publisher.py."""

import logging
from unittest.mock import MagicMock

from src.domain.events import EventType
from src.events.event_bus import EventBus
from src.infrastructure.event_publisher import (
    CompositeEventPublisher,
    DomainEventBusPublisher,
    SocketIOEventPublisher,
)


class TestSocketIOEventPublisher:
    def test_emit_delegates(self):
        socketio = MagicMock()
        pub = SocketIOEventPublisher(socketio)
        pub.emit("trade_open", {"id": "1"})
        socketio.emit.assert_called_once_with("trade_open", {"id": "1"})


class TestDomainEventBusPublisher:
    def test_emits_mapped_event(self):
        bus = EventBus()
        pub = DomainEventBusPublisher(bus)
        received = []
        bus.subscribe(EventType.TRADE_OPENED, lambda e: received.append(e))

        pub.emit("trade_open", {"trade_id": "T1"})

        assert len(received) == 1
        assert received[0].payload["trade_id"] == "T1"

    def test_stream_status_playing_true_maps_to_started(self):
        bus = EventBus()
        pub = DomainEventBusPublisher(bus)
        received = []
        bus.subscribe(EventType.STREAM_STARTED, lambda e: received.append(e))

        pub.emit("stream_status", {"playing": True})

        assert len(received) == 1

    def test_stream_status_playing_false_maps_to_paused(self):
        bus = EventBus()
        pub = DomainEventBusPublisher(bus)
        received = []
        bus.subscribe(EventType.STREAM_PAUSED, lambda e: received.append(e))

        pub.emit("stream_status", {"playing": False})

        assert len(received) == 1

    def test_readiness_changed_maps_correctly(self):
        bus = EventBus()
        pub = DomainEventBusPublisher(bus)
        received = []
        bus.subscribe(EventType.READINESS_CHANGED, lambda e: received.append(e))

        pub.emit("readiness_changed", {"state": "READY"})

        assert len(received) == 1
        assert received[0].payload["state"] == "READY"

    def test_unknown_event_is_logged(self, caplog):
        bus = EventBus()
        pub = DomainEventBusPublisher(bus)
        with caplog.at_level(logging.WARNING):
            pub.emit("unknown_event", {"x": 1})
        assert "unknown event" in caplog.text


class TestCompositeEventPublisher:
    def test_publishes_to_all_backends(self):
        first = MagicMock()
        second = MagicMock()
        pub = CompositeEventPublisher(first, second)
        pub.emit("trade_open", {"id": "1"})

        first.emit.assert_called_once_with("trade_open", {"id": "1"})
        second.emit.assert_called_once_with("trade_open", {"id": "1"})

    def test_one_failed_publisher_does_not_break_others(self, caplog):
        first = MagicMock()
        first.emit.side_effect = RuntimeError("backend down")
        second = MagicMock()
        pub = CompositeEventPublisher(first, second)

        with caplog.at_level(logging.ERROR):
            pub.emit("trade_open", {"id": "1"})

        second.emit.assert_called_once_with("trade_open", {"id": "1"})
        assert "backend down" in caplog.text
