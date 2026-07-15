"""Tests for src.domain.events."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from src.domain.events import DomainEvent, EventType


class TestEventType:
    def test_all_event_types_exist(self):
        # Spot-check a few expected members; full coverage comes from import.
        assert EventType.TRADE_OPENED
        assert EventType.TRADE_CLOSED
        assert EventType.LINE_ADDED
        assert EventType.READINESS_CHANGED
        assert EventType.BALANCE_UPDATED

    def test_event_type_values_are_unique(self):
        values = [member.value for member in EventType]
        assert len(values) == len(set(values))


class TestDomainEvent:
    def test_default_fields(self):
        event = DomainEvent(event_type=EventType.TRADE_OPENED)
        assert event.event_type == EventType.TRADE_OPENED
        assert isinstance(event.timestamp, datetime)
        assert event.payload == {}

    def test_custom_payload_and_timestamp(self):
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        event = DomainEvent(
            event_type=EventType.LINE_ADDED,
            timestamp=ts,
            payload={"id": "L1", "price": 5000.0},
        )
        assert event.timestamp == ts
        assert event.payload == {"id": "L1", "price": 5000.0}

    def test_event_is_immutable(self):
        event = DomainEvent(event_type=EventType.TRADE_CLOSED)
        with pytest.raises(FrozenInstanceError):
            event.payload = {"new": "value"}
