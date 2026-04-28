"""
ZeroMQ Trading Gateway - Core communication hub.

This module implements the ZeroMQ sockets and message routing between
Python and trading platforms.

Socket Layout:
    - PUB (5555): Market data from platform → Python
    - PULL (5556): Commands from Python → Platform
    - REQ/REP (5557): Synchronous queries
    - PUB (5558): Heartbeats (bidirectional)
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import zmq

from src.utils.app_logger import ILogger

from .protocol import (
    CloseOrderCommand,
    MessageEnvelope,
    MessageType,
    ModifyOrderCommand,
    OpenOrderCommand,
    RefreshRequestMessage,
)


@dataclass
class GatewayConfig:
    """Configuration for the TradingGateway."""

    # Socket addresses (use tcp://127.0.0.1:port for local, tcp://*:port for remote)
    market_data_pub: str = "tcp://127.0.0.1:5555"   # Platform publishes here
    command_pull: str = "tcp://127.0.0.1:5556"      # Platform listens here
    query_rep: str = "tcp://127.0.0.1:5557"         # Sync queries
    heartbeat_pub: str = "tcp://127.0.0.1:5558"     # Heartbeats

    # Connection settings
    heartbeat_interval_sec: float = 5.0
    heartbeat_timeout_sec: float = 15.0
    command_timeout_ms: int = 5000

    # Buffering
    max_queue_size: int = 10000

    # Platform binds vs connects (default: Python binds, platform connects)
    platform_connects: bool = True

    # Logging verbosity
    verbose: bool = False  # Set True for debug logging of all messages

    def get_platform_addresses(self) -> dict[str, str]:
        """Get addresses formatted for the platform (reverse if needed)."""
        if self.platform_connects:
            return {
                "market_data": self.market_data_pub.replace("*", "127.0.0.1"),
                "commands": self.command_pull.replace("*", "127.0.0.1"),
                "queries": self.query_rep.replace("*", "127.0.0.1"),
                "heartbeat": self.heartbeat_pub.replace("*", "127.0.0.1"),
            }
        else:
            return {
                "market_data": self.market_data_pub,
                "commands": self.command_pull,
                "queries": self.query_rep,
                "heartbeat": self.heartbeat_pub,
            }


class TradingGateway:
    """
    ZeroMQ gateway for trading platform communication.

    This class manages the ZeroMQ sockets and message routing. It is designed
to be:
    - Thread-safe: Can be used from multiple threads
    - Non-blocking: Uses background threads for I/O
    - Resilient: Auto-reconnection and heartbeat monitoring

    Usage:
        gateway = TradingGateway(config=GatewayConfig())
        gateway.start()

        # Register callbacks
        gateway.on_tick(lambda tick: logger.info(f"Price: {tick['price']}"))

        # Send commands
        gateway.send_open_order(
            trade_id="123",
            direction="long",
            entry_price=21000,
            stop_loss=20920,
            take_profit=21080,
            risk_points=80,
            rr_ratio=1.0,
        )

        gateway.stop()
    """

    def __init__(
        self,
        logger: ILogger,
        config: GatewayConfig | None = None,
        pair: str = "MNQ",
    ):
        self.logger = logger
        self.config = config or GatewayConfig()
        self.pair = pair

        # ZMQ context and sockets
        self._context: zmq.Context | None = None
        self._market_sub: zmq.Socket | None = None  # SUB: Receive market data
        self._command_push: zmq.Socket | None = None  # PUSH: Send commands
        self._query_rep: zmq.Socket | None = None  # REP: Handle queries from platform (when binding)
        self._query_req: zmq.Socket | None = None  # REQ: Send queries to platform (when connecting)
        self._heartbeat_sub: zmq.Socket | None = None  # SUB: Receive heartbeats

        # Threading
        self._running = False
        self._threads: list[threading.Thread] = []
        self._lock = threading.RLock()
        self._seq_num = 0

        # Message queues for thread safety
        self._command_queue: deque = deque(maxlen=self.config.max_queue_size)
        self._outbound_queue: deque = deque(maxlen=self.config.max_queue_size)

        # Callbacks
        self._callbacks: dict[MessageType, list[Callable]] = {
            MessageType.TICK: [],
            MessageType.BAR: [],
            MessageType.PARTIAL_BAR: [],
            MessageType.HISTORY_BATCH: [],
            MessageType.HISTORY_END: [],
            MessageType.ENTRY_FILL: [],
            MessageType.EXIT_FILL: [],
            MessageType.ORDER_REJECTED: [],
            MessageType.TRADE_LOG: [],
            MessageType.ERROR: [],
            MessageType.HEARTBEAT: [],
            MessageType.CONNECT: [],
            MessageType.TEST_PING: [],
            MessageType.TEST_PONG: [],
            MessageType.TEST_START: [],
            MessageType.TEST_STATUS: [],
            MessageType.TEST_RESULT: [],
            MessageType.POSITION_SYNC: [],
            MessageType.COMMAND_ACK: [],
        }

        # Query handlers - special callbacks that return data for sync queries
        self._position_query_handler: Callable[[], list[dict[str, Any]]] | None = None

        # Connection state
        self._platform_connected = False
        self._last_heartbeat_time: float | None = None
        self._platform_info: dict[str, Any] | None = None

        # Pending commands for acknowledgment tracking
        self._pending_commands: dict[int, dict[str, Any]] = {}  # seq_num -> command info
        self._command_ack_timeout_sec: float = 10.0  # Timeout for command acknowledgment

        # E2E test state tracking
        self._test_sequences: dict[str, dict[str, Any]] = {}

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """Start the gateway and all background threads."""
        if self._running:
            self.logger.warning("Gateway already running")
            return

        self.logger.info("Starting TradingGateway...")
        self._running = True

        # Create ZMQ context
        self._context = zmq.Context()

        # Create sockets based on connection mode
        if self.config.platform_connects:
            self._setup_python_binds()
        else:
            self._setup_python_connects()

        # Start background threads
        self._threads = [
            threading.Thread(target=self._market_data_loop, name="ZMQ-MarketData", daemon=True),
            threading.Thread(target=self._command_sender_loop, name="ZMQ-CommandSender", daemon=True),
            threading.Thread(target=self._heartbeat_loop, name="ZMQ-Heartbeat", daemon=True),
            threading.Thread(target=self._query_handler_loop, name="ZMQ-QueryHandler", daemon=True),
        ]

        for t in self._threads:
            t.start()

        self.logger.info("TradingGateway started. Listening on:")
        self.logger.info(f"  - Market data: {self.config.market_data_pub}")
        self.logger.info(f"  - Commands: {self.config.command_pull}")
        self.logger.info(f"  - Queries: {self.config.query_rep}")

    def stop(self) -> None:
        """Stop the gateway and cleanup resources."""
        if not self._running:
            return

        self.logger.info("Stopping TradingGateway...")
        self._running = False

        # Wait for threads to exit on their own (loops check _running and use
        # short timeouts so they should finish quickly).
        for t in self._threads:
            try:
                t.join(timeout=2.0)
            except Exception as e:
                self.logger.debug(f"Error joining thread: {e}")

        # Close sockets only after threads have exited to avoid libzmq aborts
        # when a socket is closed from one thread while another is blocked on it.
        for socket in [self._market_sub, self._command_push, self._query_rep, self._query_req,
                       self._heartbeat_sub]:
            if socket:
                try:
                    socket.close(linger=0)
                except Exception as e:
                    self.logger.debug(f"Error closing socket: {e}")

        # Terminate context
        if self._context:
            try:
                self._context.term()
            except Exception as e:
                self.logger.debug(f"Error terminating context: {e}")

        self._context = None
        self._threads = []
        self.logger.info("TradingGateway stopped")

    def _setup_python_binds(self) -> None:
        """Python binds sockets, platform connects to us."""
        # SUB socket: Receive market data from platform
        self._market_sub = self._context.socket(zmq.SUB)
        self._market_sub.bind(self.config.market_data_pub)
        self._market_sub.setsockopt_string(zmq.SUBSCRIBE, "")

        # PUSH socket: Send commands to platform
        self._command_push = self._context.socket(zmq.PUSH)
        self._command_push.bind(self.config.command_pull)

        # REP socket: Sync queries (Python acts as server - receive query, send response)
        self._query_rep = self._context.socket(zmq.REP)
        self._query_rep.bind(self.config.query_rep)

        # SUB socket: Receive heartbeats from platform
        self._heartbeat_sub = self._context.socket(zmq.SUB)
        self._heartbeat_sub.bind(self.config.heartbeat_pub)
        self._heartbeat_sub.setsockopt_string(zmq.SUBSCRIBE, "")

    def _setup_python_connects(self) -> None:
        """Platform binds sockets, Python connects to platform."""
        # SUB socket: Connect to platform's market data PUB
        self._market_sub = self._context.socket(zmq.SUB)
        self._market_sub.connect(self.config.market_data_pub)
        self._market_sub.setsockopt_string(zmq.SUBSCRIBE, "")

        # PUSH socket: Connect to platform's command PULL
        self._command_push = self._context.socket(zmq.PUSH)
        self._command_push.connect(self.config.command_pull)

        # REQ socket: Connect to platform's query REP (for Python-initiated queries)
        self._query_req = self._context.socket(zmq.REQ)
        self._query_req.connect(self.config.query_rep)

        # SUB socket: Connect to platform's heartbeat PUB
        self._heartbeat_sub = self._context.socket(zmq.SUB)
        self._heartbeat_sub.connect(self.config.heartbeat_pub)
        self._heartbeat_sub.setsockopt_string(zmq.SUBSCRIBE, "")

    # -------------------------------------------------------------------------
    # Background Loops
    # -------------------------------------------------------------------------

    def _market_data_loop(self) -> None:
        """Background thread: Receive and dispatch market data."""
        poller = zmq.Poller()
        poller.register(self._market_sub, zmq.POLLIN)
        poller.register(self._heartbeat_sub, zmq.POLLIN)

        # Stats tracking
        msg_count = 0
        last_stats_time = time.time()

        while self._running:
            try:
                # Poll with timeout to allow checking _running
                ready = poller.poll(timeout=100)
                if not ready:
                    continue

                # Check market data socket
                if self._market_sub in dict(ready):
                    msg = self._market_sub.recv_string()
                    msg_count += 1
                    self._handle_message(msg)

                # Check heartbeat socket
                if self._heartbeat_sub in dict(ready):
                    msg = self._heartbeat_sub.recv_string()
                    self._handle_heartbeat(msg)

                # Stats logging disabled to reduce noise
                now = time.time()
                if now - last_stats_time >= 30:
                    msg_count = 0
                    last_stats_time = now

            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    break
                self.logger.error(f"ZMQ error in market data loop: {e}")
            except Exception as e:
                self.logger.error(f"Error in market data loop: {e}")

    def _command_sender_loop(self) -> None:
        """Background thread: Send queued commands to platform."""
        while self._running:
            try:
                # Get command from queue (non-blocking)
                try:
                    envelope = self._command_queue.popleft()
                except IndexError:
                    time.sleep(0.001)  # 1ms sleep when idle
                    continue

                # Send command
                if self._command_push:
                    json_msg = envelope.to_json()
                    self._command_push.send_string(json_msg)
                    self.logger.debug(f"Sent command: {envelope.msg_type}")

            except Exception as e:
                self.logger.error(f"Error sending command: {e}")

    def _query_handler_loop(self) -> None:
        """Background thread: Handle synchronous queries from platform."""
        self.logger.info("Query handler loop started")

        while self._running:
            try:
                if self._query_rep is None:
                    time.sleep(0.1)
                    continue

                # Receive query from platform (blocking with timeout)
                # REP socket must receive, then send
                try:
                    self._query_rep.setsockopt(zmq.RCVTIMEO, 100)  # 100ms timeout
                    query_json = self._query_rep.recv_string()
                except zmq.Again:
                    continue

                # Parse and handle query
                envelope = MessageEnvelope.from_json(query_json)
                self.logger.debug(f"Query received: {envelope.msg_type}")

                if envelope.msg_type == MessageType.TEST_PING:
                    # Respond with pong
                    self.logger.info("🧪 TEST PING query received, sending PONG")
                    from .protocol import TestPongMessage
                    pong = TestPongMessage(timestamp=time.time())
                    resp_envelope = pong.to_envelope(seq_num=self._next_seq())
                    self._query_rep.send_string(resp_envelope.to_json())
                elif envelope.msg_type == MessageType.POSITION_QUERY:
                    # Handle position query - return open positions for crash recovery sync
                    positions = []
                    if self._position_query_handler:
                        try:
                            positions = self._position_query_handler()
                            self.logger.info(f"📊 POSITION QUERY: returning {len(positions)} open positions")
                        except Exception as e:
                            self.logger.error(f"Error in position query handler: {e}")

                    from .protocol import PositionResponseMessage
                    resp = PositionResponseMessage(positions=positions, count=len(positions))
                    resp_envelope = resp.to_envelope(seq_num=self._next_seq())
                    self._query_rep.send_string(resp_envelope.to_json())
                elif envelope.msg_type == MessageType.CONFIG_QUERY:
                    # Handle config query - return settings like account name
                    key = envelope.payload.get("key")
                    self.logger.debug(f"CONFIG QUERY for key: {key}")

                    from .protocol import ConfigResponseMessage
                    config = {}
                    if key == "account" or key == "all":
                        config["account"] = getattr(self, '_account_name', None)
                    else:
                        config[key] = None

                    resp = ConfigResponseMessage(config=config)
                    resp_envelope = resp.to_envelope(seq_num=self._next_seq())
                    self._query_rep.send_string(resp_envelope.to_json())
                else:
                    # Unknown query type
                    self.logger.warning(f"Unknown query type: {envelope.msg_type}")
                    # Send empty response to avoid blocking
                    resp_envelope = MessageEnvelope.create(
                        msg_type=MessageType.ERROR,
                        payload={"error": "Unknown query type"},
                        seq_num=self._next_seq(),
                    )
                    self._query_rep.send_string(resp_envelope.to_json())

            except Exception as e:
                self.logger.error(f"Error in query handler loop: {e}")
                time.sleep(0.1)

        self.logger.info("Query handler loop stopped")

    def _heartbeat_loop(self) -> None:
        """Background thread: Monitor platform health via heartbeats."""
        while self._running:
            try:
                # Note: Heartbeats are received from platform via _heartbeat_sub
                # This loop monitors platform health based on last received heartbeat

                # Check platform health
                if self._last_heartbeat_time:
                    elapsed = time.time() - self._last_heartbeat_time
                    if elapsed > self.config.heartbeat_timeout_sec and self._platform_connected:
                            self.logger.warning(f"Platform heartbeat timeout ({elapsed:.1f}s) - expected every {self.config.heartbeat_interval_sec}s")
                            self._platform_connected = False
                else:
                    # No heartbeat received yet after connection
                    # This is normal during initial connection phase
                    pass

                # Sleep in small increments so we can exit promptly when _running becomes False
                for _ in range(int(self.config.heartbeat_interval_sec * 10)):
                    if not self._running:
                        break
                    time.sleep(0.1)

            except Exception as e:
                self.logger.error(f"Error in heartbeat loop: {e}")
                time.sleep(1.0)

    # -------------------------------------------------------------------------
    # Message Handling
    # -------------------------------------------------------------------------

    def _handle_message(self, json_msg: str) -> None:
        """Parse and dispatch an incoming message."""
        try:
            envelope = MessageEnvelope.from_json(json_msg)
            msg_type = envelope.msg_type

            # Update sequence tracking
            self._seq_num = max(self._seq_num, envelope.seq_num)

            # Log message receipt (debug for high-frequency, info for important ones)
            if msg_type in (MessageType.TICK, MessageType.BAR, MessageType.PARTIAL_BAR):
                # Ticks and bars are too frequent - don't log individual messages
                pass
            elif msg_type in (MessageType.HISTORY_BATCH,
                              MessageType.ENTRY_FILL, MessageType.EXIT_FILL,
                              MessageType.ORDER_REJECTED, MessageType.CONNECT):
                # Important messages - always log
                payload_preview = self._format_payload_preview(envelope.payload)
                self.logger.info(f"RECV: {msg_type.value} seq={envelope.seq_num} {payload_preview}")
            else:
                # Other messages - debug level
                self.logger.debug(f"RECV: {msg_type.value} seq={envelope.seq_num}")

            # Dispatch to callbacks
            callbacks = self._callbacks.get(msg_type, [])
            for callback in callbacks:
                try:
                    callback(envelope.payload)
                except Exception as e:
                    self.logger.error(f"Callback error for {msg_type}: {e}")

            # Special handling for certain message types
            if msg_type == MessageType.CONNECT:
                self._handle_connect(envelope.payload)
            elif msg_type == MessageType.ENTRY_FILL:
                self._handle_entry_fill(envelope.payload)
            elif msg_type == MessageType.EXIT_FILL:
                self._handle_exit_fill(envelope.payload)
            elif msg_type == MessageType.ERROR:
                self._handle_error(envelope.payload)
            elif msg_type == MessageType.TEST_PING:
                self._handle_test_ping(envelope.payload)
            elif msg_type == MessageType.TEST_START:
                self._handle_test_start(envelope.payload)
            elif msg_type == MessageType.POSITION_SYNC:
                self._handle_position_sync(envelope.payload)
            elif msg_type == MessageType.COMMAND_ACK:
                self._handle_command_ack(envelope.payload)

        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON received: {e} | Raw: {json_msg[:200]}")
        except Exception as e:
            self.logger.error(f"Error handling message: {e} | Raw: {json_msg[:200]}")

    def _handle_heartbeat(self, json_msg: str) -> None:
        """Process heartbeat from platform."""
        try:
            envelope = MessageEnvelope.from_json(json_msg)
            if envelope.msg_type == MessageType.HEARTBEAT:
                prev_connected = self._platform_connected
                self._last_heartbeat_time = time.time()
                if not prev_connected:
                    self._platform_connected = True
                    self.logger.info("Platform connected (heartbeat received)")
        except Exception as e:
            self.logger.debug(f"Error handling heartbeat: {e}")

    def _handle_connect(self, payload: dict[str, Any]) -> None:
        """Handle initial connection from platform."""
        self._platform_info = payload
        self._platform_connected = True
        self._last_heartbeat_time = time.time()
        platform = payload.get('platform', 'unknown')
        version = payload.get('version', 'unknown')
        pair = payload.get('pair', 'unknown')
        account = payload.get('account', 'N/A')
        self.logger.info(f"Platform connected: {platform} v{version} | Pair: {pair} | Account: {account}")

    def _handle_entry_fill(self, payload: dict[str, Any]) -> None:
        """Handle entry fill notification."""
        self.logger.info(f"Entry fill: {payload.get('trade_id')} @ {payload.get('entry_price')}")

    def _handle_exit_fill(self, payload: dict[str, Any]) -> None:
        """Handle exit fill notification."""
        self.logger.info(f"Exit fill: {payload.get('trade_id')} @ {payload.get('exit_price')} ({payload.get('result_type')})")

    def _handle_position_sync(self, payload: dict[str, Any]) -> None:
        """Handle position sync from broker (broker is source of truth).

        This is sent by NinjaTrader after reconnect to report actual broker positions.
        Python should reconcile its state to match.
        """
        positions = payload.get('positions', [])
        count = payload.get('count', 0)
        source = payload.get('source', 'unknown')
        untracked = payload.get('untracked_orders', [])

        self.logger.info(f"📊 POSITION SYNC from {source}: {count} position(s)")

        for pos in positions:
            trade_id = pos.get('trade_id')
            direction = pos.get('direction')
            entry = pos.get('entry_price')
            self.logger.info(f"   - {trade_id}: {direction} @ {entry}")

        if untracked:
            self.logger.warning(f"   ⚠️ {len(untracked)} untracked order(s) on broker")
            for order in untracked:
                self.logger.warning(f"      - {order.get('order_name')}")

    def _handle_command_ack(self, payload: dict[str, Any]) -> None:
        """Handle command acknowledgment from platform."""
        from .protocol import CommandAckMessage

        try:
            ack = CommandAckMessage.from_payload(payload)
            seq_num = ack.seq_num

            # Check if this was a pending command
            if seq_num in self._pending_commands:
                cmd_info = self._pending_commands.pop(seq_num)
                elapsed = time.time() - cmd_info.get('sent_time', 0)

                if ack.success:
                    self.logger.info(f"✅ Command ACK: {ack.command_type} seq={seq_num} trade={ack.trade_id} ({elapsed:.2f}s)")
                else:
                    self.logger.error(f"❌ Command FAILED: {ack.command_type} seq={seq_num} error='{ack.message}' ({elapsed:.2f}s)")
            else:
                # Ack for unknown command (possibly duplicate detection or late ack)
                status = "✅" if ack.success else "❌"
                self.logger.debug(f"{status} Command ACK (unknown): {ack.command_type} seq={seq_num} trade={ack.trade_id}")

        except Exception as e:
            self.logger.error(f"Error handling command ack: {e}")

    def _handle_error(self, payload: dict[str, Any]) -> None:
        """Handle error notification from platform."""
        source = payload.get('source', 'unknown')
        error_type = payload.get('error_type', 'unknown')
        message = payload.get('message', 'No message')
        details = payload.get('details', '')
        payload.get('timestamp', 0)

        # Log with high visibility
        self.logger.error(f"PLATFORM ERROR from {source}: [{error_type}] {message}")
        if details:
            # Log details line by line for readability
            for line in details.split(' | ')[:5]:  # Limit to 5 lines
                self.logger.error(f"  → {line}")

    def _handle_test_ping(self, payload: dict[str, Any]) -> None:
        """Handle test ping - respond with pong."""
        timestamp = payload.get('timestamp', time.time())
        self.logger.info("=" * 60)
        self.logger.info("🧪 TEST PING RECEIVED FROM NINJATRADER")
        self.logger.info(f"   Timestamp: {timestamp}")
        self.logger.info("   Sending PONG response...")
        self.logger.info("=" * 60)

        # Send pong response
        from .protocol import TestPongMessage
        pong = TestPongMessage(timestamp=time.time())
        self._send_command(pong.to_envelope(seq_num=self._next_seq()))

    def _handle_test_start(self, payload: dict[str, Any]) -> None:
        """Handle E2E test start - create test trade and enqueue commands."""
        scenario = payload.get('scenario', 'tp_hit')
        entry_price = payload.get('entry_price', 21000.0)
        risk_points = payload.get('risk_points', 80.0)
        rr_ratio = payload.get('rr_ratio', 1.0)

        self.logger.info("=" * 60)
        self.logger.info(f"🧪 E2E TEST START RECEIVED: {scenario}")
        self.logger.info(f"   Entry Price: {entry_price}")
        self.logger.info(f"   Risk Points: {risk_points}")
        self.logger.info(f"   R:R Ratio: {rr_ratio}")
        self.logger.info("=" * 60)

        # Generate test trade ID
        import uuid
        trade_id = f"test_{scenario}_{uuid.uuid4().hex[:8]}"

        # Calculate SL/TP
        sl = entry_price - risk_points
        tp = entry_price + (risk_points * rr_ratio)

        self.logger.info(f"   Generated Trade ID: {trade_id}")
        self.logger.info(f"   Stop Loss: {sl}")
        self.logger.info(f"   Take Profit: {tp}")

        # Store test sequence state
        self._test_sequences[trade_id] = {
            'stage': 'awaiting_entry_fill',
            'scenario': scenario,
            'entry_price': entry_price,
            'sl': sl,
            'tp': tp,
            'start_time': time.time(),
        }

        # Send open order command to platform
        self.send_open_order(
            trade_id=trade_id,
            direction='long',
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            risk_points=risk_points,
            rr_ratio=rr_ratio,
        )

        self.logger.info(f"✅ TEST: Queued open order command for {trade_id}")

    def _format_payload_preview(self, payload: dict[str, Any]) -> str:
        """Format payload for logging (short preview)."""
        if not payload:
            return "{}"

        # Key fields to show for different message types
        key_fields = ['pair', 'price', 'time', 'trade_id', 'direction', 'entry_price',
                      'exit_price', 'result_type', 'bars', 'count', 'status']

        parts = []
        for key in key_fields:
            if key in payload:
                val = payload[key]
                if key == 'bars' and isinstance(val, list):
                    parts.append(f"bars={len(val)}")
                elif key == 'price' or key == 'entry_price' or key == 'exit_price':
                    parts.append(f"{key}={val:.2f}")
                else:
                    parts.append(f"{key}={val}")

        if not parts:
            # Fallback: show first key
            first_key = list(payload.keys())[0]
            parts.append(f"{first_key}={payload[first_key]}")

        return " | ".join(parts[:4])  # Limit to 4 parts

    # -------------------------------------------------------------------------
    # Public API - Callback Registration
    # -------------------------------------------------------------------------

    def on(self, msg_type: MessageType, callback: Callable) -> None:
        """Register a callback for a specific message type."""
        with self._lock:
            if msg_type not in self._callbacks:
                self._callbacks[msg_type] = []
            self._callbacks[msg_type].append(callback)

    def on_tick(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register tick callback."""
        self.on(MessageType.TICK, callback)

    def on_bar(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register bar callback."""
        self.on(MessageType.BAR, callback)

    def on_partial_bar(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register partial bar callback."""
        self.on(MessageType.PARTIAL_BAR, callback)

    def on_history_batch(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register history batch callback."""
        self.on(MessageType.HISTORY_BATCH, callback)

    def on_history_end(self, callback: Callable[[], None]) -> None:
        """Register history end callback."""
        self.on(MessageType.HISTORY_END, lambda _: callback())

    def on_entry_fill(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register entry fill callback."""
        self.on(MessageType.ENTRY_FILL, callback)

    def on_exit_fill(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register exit fill callback."""
        self.on(MessageType.EXIT_FILL, callback)

    def on_trade_log(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register trade log callback."""
        self.on(MessageType.TRADE_LOG, callback)

    def on_error(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register error callback for platform errors.

        Error payload contains:
            - source: Where the error originated (e.g., 'ninjatrader')
            - error_type: Type of error (e.g., 'order_open_failed')
            - message: Human-readable error message
            - details: Full exception details (optional)
            - timestamp: Unix timestamp
        """
        self.on(MessageType.ERROR, callback)

    def on_test_ping(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register test ping callback."""
        self.on(MessageType.TEST_PING, callback)

    def on_test_pong(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register test pong callback."""
        self.on(MessageType.TEST_PONG, callback)

    def on_position_query(self, callback: Callable[[], list[dict[str, Any]]]) -> None:
        """Register handler for position query (crash recovery sync).

        The callback should return a list of open position dicts with keys:
            - trade_id: str
            - direction: str ("long" or "short")
            - entry_price: float
            - stop_loss: float
            - take_profit: float
            - quantity: int (optional)
        """
        self._position_query_handler = callback

    def on_position_sync(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register callback for position sync from broker.

        Broker (NinjaTrader) is the source of truth. This is sent after
        reconnect so Python can reconcile its state to match reality.

        Payload contains:
            - positions: List of actual broker positions
            - count: Number of positions
            - source: Platform name (e.g., "ninjatrader")
            - is_source_of_truth: True (broker is authoritative)
            - untracked_orders: Optional list of orders without tracking
        """
        self.on(MessageType.POSITION_SYNC, callback)

    def on_test_start(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register test start callback.

        Payload contains:
            - scenario: Test scenario name ("tp_hit", "sl_hit", "session_end")
            - entry_price: Test entry price
            - risk_points: Risk in points
            - rr_ratio: Risk/Reward ratio
        """
        self.on(MessageType.TEST_START, callback)

    def on_test_result(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register test result callback.

        Payload contains:
            - scenario: Test scenario name
            - passed: True/False
            - trade_id: Test trade ID (if applicable)
            - message: Result message
        """
        self.on(MessageType.TEST_RESULT, callback)

    # -------------------------------------------------------------------------
    # Public API - Command Sending
    # -------------------------------------------------------------------------

    def _next_seq(self) -> int:
        """Get next sequence number."""
        with self._lock:
            self._seq_num += 1
            return self._seq_num

    def _send_command(self, envelope: MessageEnvelope) -> None:
        """Queue a command for sending."""
        if not self._running:
            self.logger.warning(f"Cannot send command: gateway not running (cmd={envelope.msg_type.value})")
            return

        # Track command for acknowledgment
        self._pending_commands[envelope.seq_num] = {
            'type': envelope.msg_type.value,
            'sent_time': time.time(),
            'payload': envelope.payload,
        }

        # Clean up old pending commands (older than 60 seconds)
        self._cleanup_pending_commands()

        self._command_queue.append(envelope)
        self.logger.debug(f"Queued: {envelope.msg_type.value} seq={envelope.seq_num}")

    def _cleanup_pending_commands(self) -> None:
        """Remove old pending commands that likely won't get acks."""
        now = time.time()
        timeout = 60.0  # Clean up commands older than 60 seconds
        to_remove = [
            seq for seq, info in self._pending_commands.items()
            if now - info.get('sent_time', 0) > timeout
        ]
        for seq in to_remove:
            cmd_info = self._pending_commands.pop(seq)
            self.logger.warning(f"Command timed out waiting for ack: {cmd_info['type']} seq={seq}")

    def send_open_order(
        self,
        trade_id: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_points: float,
        rr_ratio: float,
        pair: str | None = None,
        risk_usd: float | None = None,
        risk_pct: float | None = None,
    ) -> None:
        """Send open order command to platform."""
        cmd = OpenOrderCommand(
            trade_id=trade_id,
            pair=pair or self.pair,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_points=risk_points,
            rr_ratio=rr_ratio,
            risk_usd=risk_usd,
            risk_pct=risk_pct,
        )
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued OPEN order: {trade_id} {direction} @ {entry_price}")

    def send_close_order(self, trade_id: str, reason: str | None = None) -> None:
        """Send close order command to platform."""
        cmd = CloseOrderCommand(trade_id=trade_id, reason=reason)
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued CLOSE order: {trade_id} (reason: {reason})")

    def send_modify_order(
        self,
        trade_id: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> None:
        """Send modify order command to platform."""
        cmd = ModifyOrderCommand(trade_id=trade_id, stop_loss=stop_loss, take_profit=take_profit)
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued MODIFY order: {trade_id} SL={stop_loss} TP={take_profit}")

    def send_refresh_request(self, days: int = 1) -> None:
        """Request historical data refresh."""
        cmd = RefreshRequestMessage(days=days)
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued REFRESH request: {days} days")

    def send_error(
        self,
        source: str,
        error_type: str,
        message: str,
        details: str | None = None,
    ) -> None:
        """Send error notification to platform (bidirectional error reporting).

        This allows Python to report errors back to the platform.
        """

        payload = {
            "source": source,
            "error_type": error_type,
            "message": message,
            "timestamp": time.time(),
        }
        if details:
            payload["details"] = details

        envelope = MessageEnvelope.create(
            msg_type=MessageType.ERROR,
            payload=payload,
            seq_num=self._next_seq(),
        )
        self._send_command(envelope)
        self.logger.error(f"Sent error to platform: [{error_type}] {message}")

    def send_test_pong(self, timestamp: float) -> None:
        """Send test pong response."""
        from .protocol import TestPongMessage
        pong = TestPongMessage(timestamp=timestamp)
        envelope = pong.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.debug("Sent TEST_PONG")

    def send_test_result(
        self,
        scenario: str,
        passed: bool,
        trade_id: str | None = None,
        message: str = "",
    ) -> None:
        """Send E2E test result to platform."""
        from .protocol import TestResultMessage
        result = TestResultMessage(
            scenario=scenario,
            passed=passed,
            trade_id=trade_id,
            message=message,
        )
        envelope = result.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        status = "PASSED" if passed else "FAILED"
        self.logger.info(f"Sent TEST_RESULT: {scenario} {status}")

    # -------------------------------------------------------------------------
    # Public API - Queries
    # -------------------------------------------------------------------------

    def query_positions(self, timeout_ms: int = 5000) -> list[dict[str, Any]] | None:
        """Synchronously query current positions from platform."""
        if not self._query_req:
            return None

        try:
            envelope = MessageEnvelope.create(
                msg_type=MessageType.POSITION_QUERY,
                payload={},
            )
            self._query_req.send_string(envelope.to_json())

            # Set timeout
            self._query_req.setsockopt(zmq.RCVTIMEO, timeout_ms)

            response = self._query_req.recv_string()
            resp_envelope = MessageEnvelope.from_json(response)

            if resp_envelope.msg_type == MessageType.POSITION_RESPONSE:
                return resp_envelope.payload.get("positions", [])
            return None

        except zmq.Again:
            self.logger.warning("Position query timeout")
            return None
        except Exception as e:
            self.logger.error(f"Position query error: {e}")
            return None

    # -------------------------------------------------------------------------
    # Public API - Status
    # -------------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """Check if platform is connected and healthy."""
        return self._running and self._platform_connected

    @property
    def platform_info(self) -> dict[str, Any] | None:
        """Get information about connected platform."""
        return self._platform_info
