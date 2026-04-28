"""Typed domain events for trade and line operations.

These provide type-safe, self-documenting events for the EventBus.
Note: Due to dataclass inheritance constraints, these don't inherit from DomainEvent
but provide factory methods to convert to DomainEvent.
"""

from dataclasses import dataclass
from typing import Any

from .event_bus import DomainEvent, EventType


@dataclass(frozen=True)
class TradeOpenedEvent:
    """Event fired when a new trade is opened."""

    trade_id: str
    pair: str
    trade_type: str  # 'long' or 'short'
    entry_price: float
    stop_loss: float
    take_profit: float
    risk: float
    contracts: float
    timestamp_unix: float

    def to_domain_event(self) -> DomainEvent:
        """Convert to DomainEvent for EventBus."""
        return DomainEvent(
            event_type=EventType.TRADE_OPENED,
            payload={
                'trade_id': self.trade_id,
                'pair': self.pair,
                'type': self.trade_type,
                'entry': self.entry_price,
                'stop_loss': self.stop_loss,
                'take_profit': self.take_profit,
                'risk': self.risk,
                'contracts': self.contracts,
                'entry_time': self.timestamp_unix,
                'status': 'open',
            }
        )


@dataclass(frozen=True)
class TradeClosedEvent:
    """Event fired when a trade is closed."""

    trade_id: str
    pair: str
    trade_type: str
    exit_price: float
    exit_time: float
    result: float  # R-multiple
    result_type: str  # 'SL', 'TP', 'BE', 'SP'
    fees: float = 0.0
    pnl_usd: float = 0.0

    def to_domain_event(self) -> DomainEvent:
        """Convert to DomainEvent for EventBus."""
        return DomainEvent(
            event_type=EventType.TRADE_CLOSED,
            payload={
                'trade_id': self.trade_id,
                'pair': self.pair,
                'type': self.trade_type,
                'exit_price': self.exit_price,
                'exit_time': self.exit_time,
                'result': self.result,
                'result_type': self.result_type,
                'fees': self.fees,
                'pnl_usd': self.pnl_usd,
            }
        )


@dataclass(frozen=True)
class TradeUpdatedEvent:
    """Event fired when a trade is updated (e.g., SL moved to breakeven)."""

    trade_id: str
    updates: dict[str, Any]  # Fields that changed
    reason: str = ""  # Human-readable reason for update

    def to_domain_event(self) -> DomainEvent:
        """Convert to DomainEvent for EventBus."""
        return DomainEvent(
            event_type=EventType.TRADE_UPDATED,
            payload={
                'trade_id': self.trade_id,
                **self.updates,
            }
        )


@dataclass(frozen=True)
class LineAddedEvent:
    """Event fired when a line is added."""

    line_id: str
    pair: str
    price: float
    creation_timestamp: float

    def to_domain_event(self) -> DomainEvent:
        """Convert to DomainEvent for EventBus."""
        return DomainEvent(
            event_type=EventType.LINE_ADDED,
            payload={
                'id': self.line_id,
                'pair': self.pair,
                'price': self.price,
                'creation_date': self.creation_timestamp,
            }
        )


@dataclass(frozen=True)
class LineRemovedEvent:
    """Event fired when a line is removed."""

    line_id: str
    reason: str = "manual"  # Why the line was removed

    def to_domain_event(self) -> DomainEvent:
        """Convert to DomainEvent for EventBus."""
        return DomainEvent(
            event_type=EventType.LINE_REMOVED,
            payload={
                'id': self.line_id,
                'reason': self.reason,
            }
        )


@dataclass(frozen=True)
class LineUpdatedEvent:
    """Event fired when a line is updated."""

    line_id: str
    new_price: float
    old_price: float | None = None

    def to_domain_event(self) -> DomainEvent:
        """Convert to DomainEvent for EventBus."""
        payload = {
            'id': self.line_id,
            'price': self.new_price,
        }
        if self.old_price is not None:
            payload['old_price'] = self.old_price
        return DomainEvent(
            event_type=EventType.LINE_UPDATED,
            payload=payload
        )


# Factory functions for creating events

def create_trade_opened_event(trade_data: dict[str, Any]) -> DomainEvent:
    """Create a TradeOpenedEvent from trade dictionary.

    Args:
        trade_data: Trade dictionary from strategy

    Returns:
        DomainEvent for TRADE_OPENED
    """
    return DomainEvent(
        event_type=EventType.TRADE_OPENED,
        payload=trade_data,
    )


def create_trade_closed_event(
    trade_id: str,
    pair: str,
    trade_type: str,
    exit_price: float,
    exit_time: float,
    result: float,
    result_type: str,
    fees: float = 0.0,
    pnl_usd: float = 0.0,
) -> DomainEvent:
    """Create a TradeClosedEvent.

    Args:
        trade_id: ID of the closed trade
        pair: Trading pair
        trade_type: 'long' or 'short'
        exit_price: Price at which trade closed
        exit_time: Unix timestamp of close
        result: R-multiple result
        result_type: 'SL', 'TP', 'BE', or 'SP'
        fees: Trading fees
        pnl_usd: PnL in USD

    Returns:
        DomainEvent for TRADE_CLOSED
    """
    return DomainEvent(
        event_type=EventType.TRADE_CLOSED,
        payload={
            'trade_id': trade_id,
            'pair': pair,
            'type': trade_type,
            'exit_price': exit_price,
            'exit_time': exit_time,
            'result': result,
            'result_type': result_type,
            'fees': fees,
            'pnl_usd': pnl_usd,
        }
    )
