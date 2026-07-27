"""Tests for EventBus implementation."""

import pytest

from src.events import (
    DomainEvent,
    EventBus,
    EventType,
    TradeClosedEvent,
    TradeOpenedEvent,
)


class TestEventBus:
    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.TRADE_OPENED, handler)

        event = DomainEvent(EventType.TRADE_OPENED, payload={'trade_id': 'T123'})
        bus.publish(event)

        assert len(received) == 1
        assert received[0].payload['trade_id'] == 'T123'

    def test_multiple_handlers(self):
        bus = EventBus()
        received1 = []
        received2 = []

        def handler1(event):
            received1.append(event)

        def handler2(event):
            received2.append(event)

        bus.subscribe(EventType.TRADE_OPENED, handler1)
        bus.subscribe(EventType.TRADE_OPENED, handler2)

        event = DomainEvent(EventType.TRADE_OPENED, payload={})
        bus.publish(event)

        assert len(received1) == 1
        assert len(received2) == 1

    def test_unsubscribe(self):
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.TRADE_OPENED, handler)
        bus.unsubscribe(EventType.TRADE_OPENED, handler)

        event = DomainEvent(EventType.TRADE_OPENED, payload={})
        bus.publish(event)

        assert len(received) == 0

    def test_different_event_types(self):
        bus = EventBus()
        trade_events = []
        line_events = []

        bus.subscribe(EventType.TRADE_OPENED, lambda e: trade_events.append(e))
        bus.subscribe(EventType.LINE_ADDED, lambda e: line_events.append(e))

        bus.publish(DomainEvent(EventType.TRADE_OPENED, payload={}))
        bus.publish(DomainEvent(EventType.LINE_ADDED, payload={}))

        assert len(trade_events) == 1
        assert len(line_events) == 1

    def test_publish_typed(self):
        bus = EventBus()
        received = []

        bus.subscribe(EventType.TRADE_CLOSED, lambda e: received.append(e))

        bus.publish_typed(EventType.TRADE_CLOSED, {'result': 2.5})

        assert len(received) == 1
        assert received[0].payload['result'] == 2.5

    def test_handler_error_doesnt_stop_others(self):
        bus = EventBus()
        received = []

        def bad_handler(_event):
            raise ValueError("Oops")

        def good_handler(event):
            received.append(event)

        bus.subscribe(EventType.TRADE_OPENED, bad_handler)
        bus.subscribe(EventType.TRADE_OPENED, good_handler)

        # Should not raise despite bad_handler
        event = DomainEvent(EventType.TRADE_OPENED, payload={})
        bus.publish(event)

        assert len(received) == 1

    def test_clear(self):
        bus = EventBus()
        received = []

        bus.subscribe(EventType.TRADE_OPENED, lambda e: received.append(e))
        bus.clear()

        event = DomainEvent(EventType.TRADE_OPENED, payload={})
        bus.publish(event)

        assert len(received) == 0


class TestEventSubscriber:
    """Test subscriber objects implementing the protocol."""

    def test_subscriber_object(self):
        bus = EventBus()

        class MySubscriber:
            def __init__(self):
                self.events = []

            def on_event(self, event):
                self.events.append(event)

        subscriber = MySubscriber()
        bus.add_subscriber(EventType.TRADE_OPENED, subscriber)

        event = DomainEvent(EventType.TRADE_OPENED, payload={'id': 'T1'})
        bus.publish(event)

        assert len(subscriber.events) == 1
        assert subscriber.events[0].payload['id'] == 'T1'

    def test_remove_subscriber(self):
        bus = EventBus()

        class MySubscriber:
            def __init__(self):
                self.events = []

            def on_event(self, event):
                self.events.append(event)

        subscriber = MySubscriber()
        bus.add_subscriber(EventType.TRADE_OPENED, subscriber)
        bus.remove_subscriber(EventType.TRADE_OPENED, subscriber)

        event = DomainEvent(EventType.TRADE_OPENED, payload={})
        bus.publish(event)

        assert len(subscriber.events) == 0


class TestEventTypes:
    def test_trade_entry_updated_is_unique(self):
        """TRADE_ENTRY_UPDATED must exist and be distinct from TRADE_UPDATED."""
        assert EventType.TRADE_ENTRY_UPDATED is not None
        assert EventType.TRADE_ENTRY_UPDATED != EventType.TRADE_UPDATED
        assert EventType.TRADE_ENTRY_UPDATED != EventType.TRADE_OPENED


class TestTradeEvents:
    def test_trade_opened_event(self):
        event = TradeOpenedEvent(
            trade_id='T123',
            pair='MNQ',
            trade_type='long',
            entry_price=5000.0,
            stop_loss=4950.0,
            take_profit=5250.0,
            risk=50.0,
            contracts=2,
            timestamp_unix=1234567890.0,
        )

        domain_event = event.to_domain_event()
        assert domain_event.event_type == EventType.TRADE_OPENED
        assert domain_event.payload['trade_id'] == 'T123'
        assert domain_event.payload['entry'] == 5000.0

    def test_trade_closed_event(self):
        event = TradeClosedEvent(
            trade_id='T123',
            pair='MNQ',
            trade_type='long',
            exit_price=5250.0,
            exit_time=1234567900.0,
            result=5.0,
            result_type='TP',
            fees=3.0,
            pnl_usd=497.0,
        )

        domain_event = event.to_domain_event()
        assert domain_event.event_type == EventType.TRADE_CLOSED
        assert domain_event.payload['result_type'] == 'TP'
        assert domain_event.payload['pnl_usd'] == 497.0

    def test_event_immutable(self):
        event = DomainEvent(
            event_type=EventType.TRADE_OPENED,
            payload={'key': 'value'}
        )

        # Frozen dataclass prevents attribute modification
        with pytest.raises(AttributeError):
            event.event_type = EventType.TRADE_CLOSED

        # Note: The payload dict itself is still mutable (frozen only affects dataclass fields)
        # This is expected behavior - immutability is shallow


class TestGlobalEventBus:
    def test_get_event_bus_creates_instance(self):
        from src.events.event_bus import get_event_bus, reset_event_bus
        reset_event_bus()

        bus1 = get_event_bus()
        bus2 = get_event_bus()

        assert bus1 is bus2

    def test_reset_event_bus_creates_new_instance(self):
        from src.events.event_bus import get_event_bus, reset_event_bus
        reset_event_bus()

        bus1 = get_event_bus()
        reset_event_bus()
        bus2 = get_event_bus()

        assert bus1 is not bus2


class TestEventBusConcurrency:
    def test_concurrent_subscribe_and_publish(self):
        import threading
        import time

        bus = EventBus()
        received = []
        errors = []
        stop = threading.Event()

        def subscriber():
            while not stop.is_set():
                try:
                    bus.subscribe(EventType.TRADE_OPENED, lambda e: received.append(e))
                    time.sleep(0.0001)
                except Exception as exc:
                    errors.append(exc)

        def publisher():
            for _ in range(500):
                bus.publish(DomainEvent(EventType.TRADE_OPENED, payload={}))

        threads = [threading.Thread(target=subscriber) for _ in range(3)]
        threads.append(threading.Thread(target=publisher))
        for t in threads:
            t.start()
        publisher_thread = threads[-1]
        publisher_thread.join()
        stop.set()
        for t in threads[:-1]:
            t.join(timeout=1.0)

        assert not errors
        assert len(received) >= 500


class TestSocketIOBridge:
    def test_emit_exception_is_isolated(self):
        from src.events.event_bus import SocketIOBridge

        class _BrokenSocketIO:
            def emit(self, event, payload):
                if event == "trade_open":
                    raise RuntimeError("emit failed")

        bus = EventBus()
        bridge = SocketIOBridge(_BrokenSocketIO(), bus)
        bridge.start()

        received = []
        bus.subscribe(EventType.TRADE_OPENED, lambda e: received.append(e))

        # Should not raise; handler after bridge still runs.
        bus.publish(DomainEvent(EventType.TRADE_OPENED, payload={"trade_id": "T1"}))

        assert len(received) == 1

    def test_event_names_translate_correctly(self):
        from src.events.event_bus import SocketIOBridge

        class _RecordingSocketIO:
            def __init__(self):
                self.emitted = []

            def emit(self, event, payload, **kwargs):
                self.emitted.append((event, payload, kwargs))

        bus = EventBus()
        socketio = _RecordingSocketIO()
        bridge = SocketIOBridge(socketio, bus)
        bridge.start()

        bus.publish(DomainEvent(EventType.TRADE_OPENED, payload={"id": "1"}))
        bus.publish(DomainEvent(EventType.TRADE_CLOSED, payload={"id": "2"}))
        bus.publish(DomainEvent(EventType.LINE_ADDED, payload={"id": "3"}))

        events = [e[0] for e in socketio.emitted]
        assert "trade_open" in events
        assert "trade_close" in events
        assert "line_added" in events

    def test_readiness_changed_emits_to_instrument_room(self):
        from src.events.event_bus import SocketIOBridge

        class _RecordingSocketIO:
            def __init__(self):
                self.emitted = []

            def emit(self, event, payload, **kwargs):
                self.emitted.append((event, payload, kwargs))

        bus = EventBus()
        socketio = _RecordingSocketIO()
        bridge = SocketIOBridge(socketio, bus)
        bridge.start()

        bus.publish(DomainEvent(
            EventType.READINESS_CHANGED,
            payload={
                "previous_state": "DISCONNECTED",
                "state": "CONNECTED",
                "reason": "Platform connected",
                "pair": "MES",
            },
        ))

        readiness = [e for e in socketio.emitted if e[0] == "readiness_changed"]
        assert len(readiness) == 1
        event, payload, kwargs = readiness[0]
        assert payload["pair"] == "MES"
        assert kwargs.get("room") == "MES"

    def test_readiness_changed_without_pair_emits_globally(self):
        from src.events.event_bus import SocketIOBridge

        class _RecordingSocketIO:
            def __init__(self):
                self.emitted = []

            def emit(self, event, payload, **kwargs):
                self.emitted.append((event, payload, kwargs))

        bus = EventBus()
        socketio = _RecordingSocketIO()
        bridge = SocketIOBridge(socketio, bus)
        bridge.start()

        bus.publish(DomainEvent(
            EventType.READINESS_CHANGED,
            payload={
                "previous_state": "DISCONNECTED",
                "state": "CONNECTED",
                "reason": "Platform connected",
            },
        ))

        readiness = [e for e in socketio.emitted if e[0] == "readiness_changed"]
        assert len(readiness) == 1
        assert readiness[0][2].get("room") is None
