"""Tests for event publisher adapters and SocketIO bridge."""

import pytest

from src.domain.events import EventType
from src.events import DomainEvent, EventBus
from src.events.event_bus import SocketIOBridge
from src.infrastructure.event_publisher import DomainEventBusPublisher


class DummySocketIO:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))


class TestDomainEventBusPublisher:
    def test_trade_entry_update_maps_to_trade_entry_updated(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_ENTRY_UPDATED, lambda e: received.append(e))
        publisher = DomainEventBusPublisher(bus)

        publisher.emit("trade_entry_update", {"trade_id": "T1", "entry_price": 123.45})

        assert len(received) == 1
        assert received[0].event_type == EventType.TRADE_ENTRY_UPDATED
        assert received[0].payload["trade_id"] == "T1"
        assert received[0].payload["entry_price"] == 123.45

    def test_trade_update_maps_to_trade_updated(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_UPDATED, lambda e: received.append(e))
        publisher = DomainEventBusPublisher(bus)

        publisher.emit("trade_update", {"trade_id": "T1", "stop_loss": 90.0})

        assert len(received) == 1
        assert received[0].event_type == EventType.TRADE_UPDATED


class TestSocketIOBridge:
    def test_trade_entry_updated_emits_trade_entry_update(self):
        socketio = DummySocketIO()
        bus = EventBus()
        bridge = SocketIOBridge(socketio, bus)
        bridge.start()

        bus.publish(DomainEvent(
            EventType.TRADE_ENTRY_UPDATED,
            payload={"trade_id": "T1", "entry_price": 123.45},
        ))

        assert len(socketio.events) == 1
        assert socketio.events[0][0] == "trade_entry_update"
        assert socketio.events[0][1]["trade_id"] == "T1"
        assert socketio.events[0][1]["entry_price"] == 123.45

    def test_trade_updated_emits_trade_update(self):
        socketio = DummySocketIO()
        bus = EventBus()
        bridge = SocketIOBridge(socketio, bus)
        bridge.start()

        bus.publish(DomainEvent(
            EventType.TRADE_UPDATED,
            payload={"trade_id": "T1", "stop_loss": 90.0},
        ))

        assert len(socketio.events) == 1
        assert socketio.events[0][0] == "trade_update"

    def test_stop_unsubscribes_trade_entry_updated(self):
        socketio = DummySocketIO()
        bus = EventBus()
        bridge = SocketIOBridge(socketio, bus)
        bridge.start()
        bridge.stop()

        bus.publish(DomainEvent(
            EventType.TRADE_ENTRY_UPDATED,
            payload={"trade_id": "T1", "entry_price": 123.45},
        ))

        assert len(socketio.events) == 0

    def test_forwards_trade_open(self):
        socketio = DummySocketIO()
        bus = EventBus()
        bridge = SocketIOBridge(socketio, bus)
        bridge.start()

        bus.publish(DomainEvent(
            EventType.TRADE_OPENED,
            payload={"trade_id": "T1", "account": "Sim101"},
        ))

        assert len(socketio.events) == 1
        assert socketio.events[0][0] == "trade_open"

    def test_forwards_trade_open_with_signal_id(self):
        """Trades with a signal_id sibling tag are still forwarded."""
        socketio = DummySocketIO()
        bus = EventBus()
        bridge = SocketIOBridge(socketio, bus)
        bridge.start()

        bus.publish(DomainEvent(
            EventType.TRADE_OPENED,
            payload={"trade_id": "acct_1", "signal_id": "group_1", "account": "Sim101"},
        ))

        assert len(socketio.events) == 1
        assert socketio.events[0][0] == "trade_open"
