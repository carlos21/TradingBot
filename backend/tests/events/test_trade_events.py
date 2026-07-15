"""Tests for src.events.trade_events."""
import pytest

from src.domain.events import EventType
from src.events.trade_events import (
    LineAddedEvent,
    LineRemovedEvent,
    LineUpdatedEvent,
    TradeClosedEvent,
    TradeOpenedEvent,
    TradeUpdatedEvent,
    create_trade_closed_event,
    create_trade_opened_event,
)


class TestTradeOpenedEvent:
    def test_to_domain_event(self):
        event = TradeOpenedEvent(
            trade_id="T1",
            pair="MNQ",
            trade_type="long",
            entry_price=5000.0,
            stop_loss=4990.0,
            take_profit=5050.0,
            risk=1.0,
            contracts=2.0,
            timestamp_unix=1_700_000_000.0,
        )
        domain = event.to_domain_event()
        assert domain.event_type == EventType.TRADE_OPENED
        assert domain.payload == {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 5000.0,
            "stop_loss": 4990.0,
            "take_profit": 5050.0,
            "risk": 1.0,
            "contracts": 2.0,
            "entry_time": 1_700_000_000.0,
            "status": "open",
        }


class TestTradeClosedEvent:
    def test_to_domain_event(self):
        event = TradeClosedEvent(
            trade_id="T1",
            pair="MNQ",
            trade_type="long",
            exit_price=5050.0,
            exit_time=1_700_000_100.0,
            result=2.0,
            result_type="TP",
            fees=5.0,
            pnl_usd=100.0,
        )
        domain = event.to_domain_event()
        assert domain.event_type == EventType.TRADE_CLOSED
        assert domain.payload["exit_price"] == 5050.0
        assert domain.payload["result_type"] == "TP"
        assert domain.payload["fees"] == 5.0
        assert domain.payload["pnl_usd"] == 100.0

    def test_to_domain_event_defaults(self):
        event = TradeClosedEvent(
            trade_id="T2",
            pair="MNQ",
            trade_type="short",
            exit_price=4990.0,
            exit_time=1_700_000_200.0,
            result=-1.0,
            result_type="SL",
        )
        domain = event.to_domain_event()
        assert domain.payload["fees"] == 0.0
        assert domain.payload["pnl_usd"] == 0.0


class TestTradeUpdatedEvent:
    def test_to_domain_event(self):
        event = TradeUpdatedEvent(
            trade_id="T1",
            updates={"stop_loss": 5000.0},
            reason="breakeven",
        )
        domain = event.to_domain_event()
        assert domain.event_type == EventType.TRADE_UPDATED
        assert domain.payload == {"trade_id": "T1", "stop_loss": 5000.0}


class TestLineAddedEvent:
    def test_to_domain_event(self):
        event = LineAddedEvent(
            line_id="L1",
            pair="MNQ",
            price=5000.0,
            creation_timestamp=1_700_000_000.0,
        )
        domain = event.to_domain_event()
        assert domain.event_type == EventType.LINE_ADDED
        assert domain.payload == {
            "id": "L1",
            "pair": "MNQ",
            "price": 5000.0,
            "creation_date": 1_700_000_000.0,
        }


class TestLineRemovedEvent:
    def test_to_domain_event_default_reason(self):
        event = LineRemovedEvent(line_id="L1")
        domain = event.to_domain_event()
        assert domain.event_type == EventType.LINE_REMOVED
        assert domain.payload == {"id": "L1", "reason": "manual"}

    def test_to_domain_event_custom_reason(self):
        event = LineRemovedEvent(line_id="L1", reason="invalidated")
        domain = event.to_domain_event()
        assert domain.payload["reason"] == "invalidated"


class TestLineUpdatedEvent:
    def test_to_domain_event_with_old_price(self):
        event = LineUpdatedEvent(line_id="L1", new_price=5100.0, old_price=5000.0)
        domain = event.to_domain_event()
        assert domain.event_type == EventType.LINE_UPDATED
        assert domain.payload == {"id": "L1", "price": 5100.0, "old_price": 5000.0}

    def test_to_domain_event_without_old_price(self):
        event = LineUpdatedEvent(line_id="L1", new_price=5100.0)
        domain = event.to_domain_event()
        assert domain.payload == {"id": "L1", "price": 5100.0}
        assert "old_price" not in domain.payload


class TestFactoryFunctions:
    def test_create_trade_opened_event(self):
        trade_data = {"trade_id": "T1", "pair": "MNQ"}
        domain = create_trade_opened_event(trade_data)
        assert domain.event_type == EventType.TRADE_OPENED
        assert domain.payload == trade_data

    def test_create_trade_closed_event(self):
        domain = create_trade_closed_event(
            trade_id="T1",
            pair="MNQ",
            trade_type="long",
            exit_price=5050.0,
            exit_time=1_700_000_100.0,
            result=2.0,
            result_type="TP",
        )
        assert domain.event_type == EventType.TRADE_CLOSED
        assert domain.payload["result_type"] == "TP"
        assert domain.payload["fees"] == 0.0

    def test_create_trade_closed_event_with_fees_and_pnl(self):
        domain = create_trade_closed_event(
            trade_id="T1",
            pair="MNQ",
            trade_type="long",
            exit_price=5050.0,
            exit_time=1_700_000_100.0,
            result=2.0,
            result_type="TP",
            fees=2.5,
            pnl_usd=50.0,
        )
        assert domain.payload["fees"] == 2.5
        assert domain.payload["pnl_usd"] == 50.0
