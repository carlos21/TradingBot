"""
Trading Protocol Schema - Universal message format for ZeroMQ communication.

This module defines the message types and schemas used for communication
between Python and trading platforms (NinjaTrader, MetaTrader, etc.).

All messages are JSON-serializable with a consistent envelope format.
"""

from __future__ import annotations
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Optional, Literal
import time
import json


class MessageType(str, Enum):
    """Message types for the trading protocol."""
    
    # Market Data (Platform → Python)
    TICK = "tick"           # Single price tick
    BAR = "bar"             # Completed OHLCV bar
    PARTIAL_BAR = "partial" # In-progress bar (for UI)
    HISTORY_BATCH = "history_batch"  # Batch of historical bars
    HISTORY_END = "history_end"      # End of historical data
    
    # Trade Commands (Python → Platform)
    ORDER_OPEN = "order_open"
    ORDER_CLOSE = "order_close"
    ORDER_MODIFY = "order_modify"
    
    # Trade Events (Platform → Python)
    ENTRY_FILL = "entry_fill"   # Position opened
    EXIT_FILL = "exit_fill"     # Position closed (SL/TP/Manual)
    ORDER_REJECTED = "order_rejected"
    
    # Logging (Platform → Python)
    TRADE_LOG = "trade_log"     # Generic trade lifecycle log
    ERROR = "error"             # Error from platform
    WARNING = "warning"         # Warning from platform
    
    # Control
    HEARTBEAT = "heartbeat"
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    REFRESH_REQUEST = "refresh_request"
    REFRESH_START = "refresh_start"
    
    # Queries (Bidirectional)
    POSITION_QUERY = "position_query"
    POSITION_RESPONSE = "position_response"
    ACCOUNT_QUERY = "account_query"
    ACCOUNT_RESPONSE = "account_response"
    CONFIG_QUERY = "config_query"
    CONFIG_RESPONSE = "config_response"
    
    # Position Sync (Platform → Python, broker is source of truth)
    POSITION_SYNC = "position_sync"
    
    # Command Acknowledgment (Platform → Python)
    COMMAND_ACK = "command_ack"
    
    # Testing (Bidirectional)
    TEST_PING = "test_ping"           # Connection test request
    TEST_PONG = "test_pong"           # Connection test response
    TEST_START = "test_start"         # Start E2E test scenario
    TEST_STATUS = "test_status"       # Test progress/status update
    TEST_RESULT = "test_result"       # Final test result


@dataclass
class MessageEnvelope:
    """
    Standard message envelope for all protocol messages.
    
    Every message has this structure:
    {
        "msg_type": "tick",
        "timestamp": 1712789432.123456,
        "seq_num": 12345,
        "payload": { ... }
    }
    """
    msg_type: MessageType
    timestamp: float
    seq_num: int
    payload: Dict[str, Any]
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "msg_type": self.msg_type.value,
            "timestamp": self.timestamp,
            "seq_num": self.seq_num,
            "payload": self.payload,
        })
    
    @classmethod
    def from_json(cls, json_str: str) -> MessageEnvelope:
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls(
            msg_type=MessageType(data["msg_type"]),
            timestamp=data["timestamp"],
            seq_num=data["seq_num"],
            payload=data["payload"],
        )
    
    @classmethod
    def create(
        cls,
        msg_type: MessageType,
        payload: Dict[str, Any],
        seq_num: int = 0,
    ) -> MessageEnvelope:
        """Create a new message envelope with current timestamp."""
        return cls(
            msg_type=msg_type,
            timestamp=time.time(),
            seq_num=seq_num,
            payload=payload,
        )


# =============================================================================
# Market Data Messages (Platform → Python)
# =============================================================================

@dataclass
class TickMessage:
    """Single price tick."""
    pair: str
    price: float
    volume: int
    time: int  # Unix timestamp (seconds)
    bid: Optional[float] = None
    ask: Optional[float] = None
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.TICK,
            payload={
                "pair": self.pair,
                "price": self.price,
                "volume": self.volume,
                "time": self.time,
                **({"bid": self.bid} if self.bid is not None else {}),
                **({"ask": self.ask} if self.ask is not None else {}),
            },
            seq_num=seq_num,
        )


@dataclass
class BarMessage:
    """OHLCV bar - completed or partial."""
    pair: str
    time: int  # Unix timestamp (seconds) - bar open time
    open: float
    high: float
    low: float
    close: float
    volume: int
    is_partial: bool = False  # True for in-progress bar
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        msg_type = MessageType.PARTIAL_BAR if self.is_partial else MessageType.BAR
        return MessageEnvelope.create(
            msg_type=msg_type,
            payload={
                "pair": self.pair,
                "time": self.time,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
            },
            seq_num=seq_num,
        )


@dataclass
class HistoryBatchMessage:
    """Batch of historical bars."""
    pair: str
    bars: list[Dict[str, Any]]  # List of OHLCV dicts
    days: int  # How many days of history
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.HISTORY_BATCH,
            payload={
                "pair": self.pair,
                "bars": self.bars,
                "days": self.days,
            },
            seq_num=seq_num,
        )


# =============================================================================
# Trade Command Messages (Python → Platform)
# =============================================================================

@dataclass
class OpenOrderCommand:
    """Command to open a new position."""
    trade_id: str
    pair: str
    direction: Literal["long", "short"]
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_points: float  # Distance from entry to SL in points
    rr_ratio: float     # Risk:Reward ratio
    risk_usd: Optional[float] = None   # For position sizing
    risk_pct: Optional[float] = None   # Alternative: % of account
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        payload = {
            "trade_id": self.trade_id,
            "pair": self.pair,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_points": self.risk_points,
            "rr_ratio": self.rr_ratio,
        }
        if self.risk_usd is not None:
            payload["risk_usd"] = self.risk_usd
        if self.risk_pct is not None:
            payload["risk_pct"] = self.risk_pct
            
        return MessageEnvelope.create(
            msg_type=MessageType.ORDER_OPEN,
            payload=payload,
            seq_num=seq_num,
        )


@dataclass
class CloseOrderCommand:
    """Command to close a position."""
    trade_id: str
    reason: Optional[str] = None  # "session_end", "manual", "strategy"
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.ORDER_CLOSE,
            payload={
                "trade_id": self.trade_id,
                **({"reason": self.reason} if self.reason else {}),
            },
            seq_num=seq_num,
        )


@dataclass
class ModifyOrderCommand:
    """Command to modify an existing order (e.g., move SL to breakeven)."""
    trade_id: str
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        payload = {"trade_id": self.trade_id}
        if self.stop_loss is not None:
            payload["stop_loss"] = self.stop_loss
        if self.take_profit is not None:
            payload["take_profit"] = self.take_profit
            
        return MessageEnvelope.create(
            msg_type=MessageType.ORDER_MODIFY,
            payload=payload,
            seq_num=seq_num,
        )


# =============================================================================
# Fill/Event Messages (Platform → Python)
# =============================================================================

@dataclass
class EntryFillMessage:
    """Position entry fill notification."""
    trade_id: str
    entry_price: float          # Actual fill price
    stop_loss: Optional[float] = None   # Broker-calculated SL
    take_profit: Optional[float] = None # Broker-calculated TP
    slippage: Optional[float] = None    # Difference from requested entry
    contracts: Optional[int] = None
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        payload = {
            "trade_id": self.trade_id,
            "entry_price": self.entry_price,
        }
        if self.stop_loss is not None:
            payload["stop_loss"] = self.stop_loss
        if self.take_profit is not None:
            payload["take_profit"] = self.take_profit
        if self.slippage is not None:
            payload["slippage"] = self.slippage
        if self.contracts is not None:
            payload["contracts"] = self.contracts
            
        return MessageEnvelope.create(
            msg_type=MessageType.ENTRY_FILL,
            payload=payload,
            seq_num=seq_num,
        )


@dataclass
class ExitFillMessage:
    """Position exit fill notification (SL, TP, or manual close)."""
    trade_id: str
    exit_price: float
    result_type: Literal["SL", "TP", "SP", "CLOSE"]  # SL=Stop Loss, TP=Take Profit, SP=Session End, CLOSE=Manual
    exit_time: Optional[int] = None  # Unix timestamp
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        payload = {
            "trade_id": self.trade_id,
            "exit_price": self.exit_price,
            "result_type": self.result_type,
        }
        if self.exit_time is not None:
            payload["exit_time"] = self.exit_time
            
        return MessageEnvelope.create(
            msg_type=MessageType.EXIT_FILL,
            payload=payload,
            seq_num=seq_num,
        )


@dataclass
class OrderRejectedMessage:
    """Order rejected by broker/platform."""
    trade_id: str
    reason: str
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.ORDER_REJECTED,
            payload={
                "trade_id": self.trade_id,
                "reason": self.reason,
            },
            seq_num=seq_num,
        )


# =============================================================================
# Logging Messages (Platform → Python)
# =============================================================================

@dataclass
class TradeLogMessage:
    """Generic trade lifecycle log entry."""
    trade_id: str
    event: str      # "NT:ORDER", "NT:FILL", "NT:MODIFY", etc.
    message: str
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.TRADE_LOG,
            payload={
                "trade_id": self.trade_id,
                "event": self.event,
                "message": self.message,
            },
            seq_num=seq_num,
        )


# =============================================================================
# Control Messages
# =============================================================================

@dataclass
class HeartbeatMessage:
    """Keep-alive message."""
    source: str  # "python" or platform name
    status: str  # "ok", "busy", "error"
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.HEARTBEAT,
            payload={
                "source": self.source,
                "status": self.status,
            },
            seq_num=seq_num,
        )


@dataclass
class ConnectMessage:
    """Initial connection handshake."""
    platform: str           # "ninjatrader", "metatrader5", etc.
    version: str            # Protocol version
    account: Optional[str] = None
    pair: Optional[str] = None
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        payload = {
            "platform": self.platform,
            "version": self.version,
        }
        if self.account is not None:
            payload["account"] = self.account
        if self.pair is not None:
            payload["pair"] = self.pair
            
        return MessageEnvelope.create(
            msg_type=MessageType.CONNECT,
            payload=payload,
            seq_num=seq_num,
        )


@dataclass
class RefreshRequestMessage:
    """Request historical data refresh."""
    days: int = 1
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.REFRESH_REQUEST,
            payload={"days": self.days},
            seq_num=seq_num,
        )


# =============================================================================
# Test Messages
# =============================================================================

@dataclass
class TestPingMessage:
    """Connection test ping."""
    timestamp: float
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.TEST_PING,
            payload={"timestamp": self.timestamp},
            seq_num=seq_num,
        )


@dataclass
class TestPongMessage:
    """Connection test response."""
    timestamp: float
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.TEST_PONG,
            payload={"timestamp": self.timestamp},
            seq_num=seq_num,
        )


@dataclass
class TestStartMessage:
    """Start E2E test scenario."""
    scenario: str  # "tp_hit", "sl_hit", "session_end"
    entry_price: float = 21000.0
    risk_points: float = 80.0
    rr_ratio: float = 1.0
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.TEST_START,
            payload={
                "scenario": self.scenario,
                "entry_price": self.entry_price,
                "risk_points": self.risk_points,
                "rr_ratio": self.rr_ratio,
            },
            seq_num=seq_num,
        )


@dataclass
class TestResultMessage:
    """E2E test result."""
    scenario: str
    passed: bool
    trade_id: Optional[str] = None
    message: str = ""
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        payload = {
            "scenario": self.scenario,
            "passed": self.passed,
            "message": self.message,
        }
        if self.trade_id:
            payload["trade_id"] = self.trade_id
        return MessageEnvelope.create(
            msg_type=MessageType.TEST_RESULT,
            payload=payload,
            seq_num=seq_num,
        )


# =============================================================================
# Query Messages (Bidirectional)
# =============================================================================

@dataclass
class PositionQueryMessage:
    """Query for current open positions (used for crash recovery sync)."""
    pair: Optional[str] = None  # Filter by pair, or None for all
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        payload = {}
        if self.pair is not None:
            payload["pair"] = self.pair
        return MessageEnvelope.create(
            msg_type=MessageType.POSITION_QUERY,
            payload=payload,
            seq_num=seq_num,
        )


@dataclass
class PositionResponseMessage:
    """Response to position query with current open trades."""
    positions: list[Dict[str, Any]]  # List of position dicts with trade_id, entry, sl, tp, etc.
    count: int
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.POSITION_RESPONSE,
            payload={
                "positions": self.positions,
                "count": self.count,
            },
            seq_num=seq_num,
        )


@dataclass
class PositionSyncMessage:
    """Position sync from broker (broker is source of truth).
    
    Sent by platform after reconnect to report actual broker positions.
    Python should reconcile its state to match.
    """
    positions: list[Dict[str, Any]]  # Actual broker positions
    count: int
    source: str  # Platform name (e.g., "ninjatrader")
    is_source_of_truth: bool = True
    untracked_orders: Optional[list[Dict[str, Any]]] = None
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        payload = {
            "positions": self.positions,
            "count": self.count,
            "source": self.source,
            "is_source_of_truth": self.is_source_of_truth,
        }
        if self.untracked_orders:
            payload["untracked_orders"] = self.untracked_orders
            
        return MessageEnvelope.create(
            msg_type=MessageType.POSITION_SYNC,
            payload=payload,
            seq_num=seq_num,
        )


@dataclass
class CommandAckMessage:
    """Command acknowledgment from platform.
    
    Sent by platform after processing a command to confirm receipt.
    """
    command_type: str  # e.g., "order_open", "order_modify"
    seq_num: int
    success: bool
    trade_id: Optional[str] = None
    message: Optional[str] = None
    timestamp: Optional[float] = None
    
    def to_envelope(self, seq_num_out: int = 0) -> MessageEnvelope:
        payload = {
            "command_type": self.command_type,
            "seq_num": self.seq_num,
            "success": self.success,
        }
        if self.trade_id is not None:
            payload["trade_id"] = self.trade_id
        if self.message is not None:
            payload["message"] = self.message
        if self.timestamp is not None:
            payload["timestamp"] = self.timestamp
            
        return MessageEnvelope.create(
            msg_type=MessageType.COMMAND_ACK,
            payload=payload,
            seq_num=seq_num_out,
        )
    
    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "CommandAckMessage":
        """Create from received payload."""
        return cls(
            command_type=payload.get("command_type", ""),
            seq_num=payload.get("seq_num", 0),
            success=payload.get("success", False),
            trade_id=payload.get("trade_id"),
            message=payload.get("message"),
            timestamp=payload.get("timestamp"),
        )


@dataclass
class ConfigResponseMessage:
    """Response to config query with settings like account name."""
    config: Dict[str, Any]
    
    def to_envelope(self, seq_num: int = 0) -> MessageEnvelope:
        return MessageEnvelope.create(
            msg_type=MessageType.CONFIG_RESPONSE,
            payload=self.config,
            seq_num=seq_num,
        )


# =============================================================================
# Convenience type unions
# =============================================================================

TradeCommand = OpenOrderCommand | CloseOrderCommand | ModifyOrderCommand
MarketDataMessage = TickMessage | BarMessage | HistoryBatchMessage
FillMessage = EntryFillMessage | ExitFillMessage | OrderRejectedMessage
PositionQuery = PositionQueryMessage | PositionResponseMessage
