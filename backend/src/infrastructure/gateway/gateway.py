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

import contextlib
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
    AuditRequestMessage,
    CloseOrderCommand,
    DisconnectMessage,
    MessageEnvelope,
    MessageType,
    ModifyOrderCommand,
    OpenOrderCommand,
    RefreshRequestMessage,
    SubscribeMessage,
    UnsubscribeMessage,
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
    ):
        self.logger = logger
        self.config = config or GatewayConfig()

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
        self._query_lock = threading.Lock()  # Dedicated lock for REQ socket operations
        self._seq_num = 0
        self._cleanup_counter = 0  # Throttle _cleanup_pending_commands
        self._last_cleanup_time: float = 0.0  # Time-based throttle for cleanup

        # Message queues for thread safety
        self._command_queue: deque = deque(maxlen=self.config.max_queue_size)
        self._command_ready = threading.Event()  # Signal when command is enqueued

        # Retry tracking for failed command sends
        self._command_retries: dict[int, int] = {}

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
            MessageType.POSITION_SYNC: [],
            MessageType.COMMAND_ACK: [],
            MessageType.MARKET_STATUS: [],
            MessageType.AUDIT_RESPONSE: [],
        }

        # Query handlers - special callbacks that return data for sync queries
        self._position_query_handler: Callable[[], list[dict[str, Any]]] | None = None

        # Connection state
        self._platform_connected = False
        self._last_heartbeat_time: float | None = None
        self._platform_info: dict[str, Any] | None = None
        self._connection_listeners: list[Callable[[bool], None]] = []
        self._disconnect_time: float | None = None  # When disconnect was detected
        self._reconnect_debounce_sec: float = 3.0  # Minimum disconnect duration before treating it as a real disconnect
        self._last_disconnect_was_real: bool = True  # Used by data source to decide whether to refresh history

        # Pending commands for acknowledgment tracking
        self._pending_commands: dict[int, dict[str, Any]] = {}  # seq_num -> command info
        self._command_ack_timeout_sec: float = 10.0  # Timeout for command acknowledgment

        # Per-entry latency instrumentation: trade_id -> timing record populated by
        # send_open_order/_handle_command_ack and consumed by _handle_entry_fill
        self._entry_order_timing: dict[str, dict[str, Any]] = {}

        # Callback for permanently dropped commands (after max retries)
        self._on_command_dropped: Callable[[str, dict[str, Any]], None] | None = None

        # Callbacks notified when a command is NACK'd or times out waiting for an ACK
        self._command_failure_listeners: list[Callable[[str, str, int, str], None]] = []

        # Callbacks notified for EVERY ACK timeout — including trade_id-less
        # infra commands (subscribe/refresh_request) that on_command_failed drops
        self._command_timeout_listeners: list[Callable[[str, dict[str, Any], int], None]] = []

        # Account names reported in config queries (refreshed from DB on demand)
        self._account_names: list[str] = []

    def set_account_names(self, names: list[str]) -> None:
        """Set the account names used for config queries from the platform."""
        self._account_names = list(names)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def _refresh_account_names(self) -> None:
        """Reload account names from DB so changes take effect without restart."""
        try:
            from src.infrastructure.repositories.accounts_repository import (
                NtAccountRepository,
            )
            repo = NtAccountRepository()
            accounts = repo.list_accounts()
            self._account_names = [a.name for a in accounts]
        except Exception as e:
            self.logger.warning(f"Failed to refresh account names from DB: {e}")

    def start(self) -> None:
        """Start the gateway and all background threads."""
        with self._lock:
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
        with self._lock:
            if not self._running:
                return

            self.logger.info("Stopping TradingGateway...")
            self._running = False

        # Wake the command sender so it notices _running == False immediately
        # instead of waiting on _command_ready.
        self._command_ready.set()

        # Wait for background threads to finish. All loops use short timeouts
        # (poll/rcvtimeo/event wait) except command send, which can block for
        # SNDTIMEO. Use a timeout longer than that so we do not close sockets
        # while a thread is still inside a socket call (closing a socket from
        # another thread during an active call can segfault).
        for t in self._threads:
            try:
                t.join(timeout=6.0)
            except Exception as e:
                self.logger.debug(f"Error joining thread: {e}")

        # Close sockets now that threads have exited.
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

        # Clear all state so restart is clean
        with self._lock:
            self._context = None
            self._threads = []
            self._pending_commands.clear()
            self._command_retries.clear()
            self._command_queue.clear()
            self._seq_num = 0
            self._platform_connected = False
            self._platform_info = None
        self.logger.info("TradingGateway stopped")

    def _setup_python_binds(self) -> None:
        """Python binds sockets, platform connects to us."""
        # SUB socket: Receive market data from platform
        self._market_sub = self._context.socket(zmq.SUB)
        self._market_sub.setsockopt(zmq.RCVHWM, 5000)
        self._market_sub.bind(self.config.market_data_pub)
        self._market_sub.setsockopt_string(zmq.SUBSCRIBE, "")

        # PUSH socket: Send commands to platform
        self._command_push = self._context.socket(zmq.PUSH)
        self._command_push.setsockopt(zmq.SNDTIMEO, 5000)  # Prevent infinite block on disconnect
        self._command_push.bind(self.config.command_pull)

        # REP socket: Sync queries (Python acts as server - receive query, send response)
        self._query_rep = self._context.socket(zmq.REP)
        self._query_rep.bind(self.config.query_rep)

        # SUB socket: Receive heartbeats from platform
        self._heartbeat_sub = self._context.socket(zmq.SUB)
        self._heartbeat_sub.setsockopt(zmq.RCVHWM, 1000)
        self._heartbeat_sub.bind(self.config.heartbeat_pub)
        self._heartbeat_sub.setsockopt_string(zmq.SUBSCRIBE, "")

    def _setup_python_connects(self) -> None:
        """Platform binds sockets, Python connects to platform."""
        # SUB socket: Connect to platform's market data PUB
        self._market_sub = self._context.socket(zmq.SUB)
        self._market_sub.setsockopt(zmq.RCVHWM, 5000)
        self._market_sub.connect(self.config.market_data_pub)
        self._market_sub.setsockopt_string(zmq.SUBSCRIBE, "")

        # PUSH socket: Connect to platform's command PULL
        self._command_push = self._context.socket(zmq.PUSH)
        self._command_push.setsockopt(zmq.SNDTIMEO, 5000)  # Prevent infinite block on disconnect
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
                # Get command from queue (wait for signal)
                try:
                    envelope = self._command_queue.popleft()
                except IndexError:
                    # Clear the event only if the queue is still empty while holding
                    # the lock — _send_command appends + sets the event under the same
                    # lock, so this cannot wipe a pending wakeup (missed-wakeup race).
                    with self._lock:
                        if not self._command_queue:
                            self._command_ready.clear()
                    self._command_ready.wait(timeout=0.1)
                    continue

                # Send command
                if self._command_push:
                    json_msg = envelope.to_json()
                    try:
                        self._command_push.send_string(json_msg)
                        self.logger.debug(f"Sent command: {envelope.msg_type}")
                        with self._lock:
                            self._command_retries.pop(envelope.seq_num, None)
                            info = self._pending_commands.get(envelope.seq_num)
                            if info is not None:
                                info['wire_time'] = time.time()
                    except Exception as send_ex:
                        with self._lock:
                            retries = self._command_retries.get(envelope.seq_num, 0) + 1
                            if retries <= 3:
                                self._command_retries[envelope.seq_num] = retries
                                self._command_queue.appendleft(envelope)
                                self.logger.warning(f"Command send failed (retry {retries}/3): {envelope.msg_type} seq={envelope.seq_num} — re-queued: {send_ex}")
                            else:
                                self._command_retries.pop(envelope.seq_num, None)
                                cmd_info = self._pending_commands.pop(envelope.seq_num, None)
                                self.logger.error(f"Command send failed permanently after 3 retries: {envelope.msg_type} seq={envelope.seq_num}: {send_ex}")
                                trade_id = cmd_info.get("payload", {}).get("trade_id") if cmd_info else None
                                self._notify_command_failed(
                                    envelope.msg_type.value,
                                    trade_id,
                                    envelope.seq_num,
                                    f"send_failed:{send_ex}",
                                )
                                if self._on_command_dropped and cmd_info:
                                    with contextlib.suppress(Exception):
                                        self._on_command_dropped(envelope.msg_type.value, cmd_info)
                else:
                    cmd_info = self._pending_commands.pop(envelope.seq_num, None)
                    self.logger.error(f"Command push socket not available, dropping command: {envelope.msg_type}")
                    trade_id = cmd_info.get("payload", {}).get("trade_id") if cmd_info else None
                    self._notify_command_failed(
                        envelope.msg_type.value,
                        trade_id,
                        envelope.seq_num,
                        "no_socket",
                    )

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
                # REP socket is now in "received" state — we MUST send a reply
                # before receiving again, even if parsing/handling fails.
                try:
                    envelope = MessageEnvelope.from_json(query_json)

                    if envelope.msg_type == MessageType.TEST_PING:
                        # Respond with pong
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
                        if key == "accounts" or key == "all":
                            # Refresh from DB so account changes don't require app restart
                            self._refresh_account_names()
                            account_names = self._account_names
                            config["accounts"] = ",".join(account_names) if account_names else ""
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
                    self.logger.error(f"Error handling query: {e}")
                    # Must send a reply to keep REP socket state machine valid
                    try:
                        resp_envelope = MessageEnvelope.create(
                            msg_type=MessageType.ERROR,
                            payload={"error": f"Internal error: {e}"},
                            seq_num=self._next_seq(),
                        )
                        self._query_rep.send_string(resp_envelope.to_json())
                    except Exception as send_err:
                        self.logger.error(f"Failed to send error reply on REP socket: {send_err} — recreating socket")
                        # Recreate the REP socket to reset its state machine
                        try:
                            old_rep = self._query_rep
                            self._query_rep = self._context.socket(zmq.REP)
                            self._query_rep.bind(self.config.query_rep)
                            old_rep.close(linger=0)
                            self.logger.info("REP socket recreated successfully")
                        except Exception as rec_err:
                            self.logger.error(f"Failed to recreate REP socket: {rec_err}")

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
                            self._disconnect_time = time.time()
                            self._last_disconnect_was_real = True
                            for cb in self._connection_listeners:
                                with contextlib.suppress(Exception):
                                    cb(False)
                else:
                    # No heartbeat received yet after connection
                    # This is normal during initial connection phase
                    pass

                # Sleep in small increments so we can exit promptly when _running becomes False
                for _ in range(int(self.config.heartbeat_interval_sec * 10)):
                    if not self._running:
                        break
                    time.sleep(0.1)
                    # Reap ACK-timed-out commands promptly (~every 2s) even
                    # when no new command is sent — the lazy cleanup in
                    # _send_command can otherwise leave timeouts undetected
                    # for minutes.
                    now = time.time()
                    if now - self._last_cleanup_time >= 2.0:
                        with self._lock:
                            self._last_cleanup_time = now
                            self._cleanup_pending_commands()

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
        except json.JSONDecodeError as e:
            raw_bytes = json_msg.encode('utf-8', errors='replace')
            self.logger.error(f"Invalid JSON received: {e} | Raw: {json_msg[:200]} | Hex: {raw_bytes[:200].hex()}")
            return
        except (KeyError, ValueError) as e:
            raw_bytes = json_msg.encode('utf-8', errors='replace')
            self.logger.error(f"Invalid message envelope: {e} | Raw: {json_msg[:200]} | Hex: {raw_bytes[:200].hex()}")
            return

        try:
            # Update sequence tracking (must hold lock to avoid RMW race with _next_seq)
            with self._lock:
                self._seq_num = max(self._seq_num, envelope.seq_num)

            # Log message receipt (debug for high-frequency, info for important ones)
            if msg_type == MessageType.TICK:
                # Ticks are too frequent - don't log individual messages
                pass
            elif msg_type == MessageType.BAR:
                # Completed bars: log at debug so we can trace live-stream health
                self.logger.debug(
                    f"RECV: {msg_type.value} seq={envelope.seq_num} "
                    f"time={envelope.payload.get('time')} pair={envelope.payload.get('pair')}"
                )
            elif msg_type == MessageType.PARTIAL_BAR:
                # Partial bars are too frequent - don't log individual messages
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

            # Dispatch to callbacks (snapshot under lock for thread safety
            # since on() can register callbacks from other threads)
            with self._lock:
                callbacks = list(self._callbacks.get(msg_type, []))
            for callback in callbacks:
                try:
                    callback(envelope.payload)
                except Exception as e:
                    self.logger.error(f"Callback error for {msg_type}: {e}")

            # Special handling for certain message types
            if msg_type == MessageType.MARKET_STATUS:
                self._handle_market_status(envelope.payload)
            elif msg_type == MessageType.CONNECT:
                self._handle_connect(envelope.payload)
            elif msg_type == MessageType.ENTRY_FILL:
                self._handle_entry_fill(envelope.payload)
            elif msg_type == MessageType.EXIT_FILL:
                self._handle_exit_fill(envelope.payload)
            elif msg_type == MessageType.ERROR:
                self._handle_error(envelope.payload)
            elif msg_type == MessageType.TEST_PING:
                self._handle_test_ping(envelope.payload)
            elif msg_type == MessageType.POSITION_SYNC:
                self._handle_position_sync(envelope.payload)
            elif msg_type == MessageType.COMMAND_ACK:
                self._handle_command_ack(envelope.payload)

        except Exception as e:
            raw_bytes = json_msg.encode('utf-8', errors='replace')
            self.logger.error(f"Error handling message: {e} | Raw: {json_msg[:200]} | Hex: {raw_bytes[:200].hex()}")

    def _handle_heartbeat(self, json_msg: str) -> None:
        """Process heartbeat from platform."""
        try:
            envelope = MessageEnvelope.from_json(json_msg)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raw_bytes = json_msg.encode('utf-8', errors='replace') if isinstance(json_msg, str) else json_msg
            self.logger.debug(f"Invalid heartbeat envelope: {e} | Hex: {raw_bytes[:200].hex() if isinstance(raw_bytes, bytes) else raw_bytes}")
            return

        try:
            if envelope.msg_type == MessageType.HEARTBEAT:
                prev_connected = self._platform_connected
                self._last_heartbeat_time = time.time()
                if not prev_connected:
                    self._platform_connected = True
                    # The data source uses this flag to decide whether to auto-refresh
                    # history after a reconnect. We always notify listeners so the
                    # readiness state machine and frontend stay in sync.
                    was_real_disconnect = (
                        self._disconnect_time is None  # first connection ever
                        or (time.time() - self._disconnect_time) >= self._reconnect_debounce_sec
                    )
                    self._disconnect_time = None
                    self._last_disconnect_was_real = was_real_disconnect
                    if was_real_disconnect:
                        self.logger.info("Platform connected (heartbeat received)")
                    else:
                        self.logger.info("Platform reconnected after brief blip — skipping history refresh")
                    for cb in self._connection_listeners:
                        with contextlib.suppress(Exception):
                            cb(True)
        except Exception as e:
            raw_bytes = json_msg.encode('utf-8', errors='replace') if isinstance(json_msg, str) else json_msg
            self.logger.debug(f"Error handling heartbeat: {e} | Hex: {raw_bytes[:200].hex() if isinstance(raw_bytes, bytes) else raw_bytes}")

    def _handle_market_status(self, payload: dict[str, Any]) -> None:
        """Handle market status notification from platform."""
        market_open = payload.get("market_open", True)
        next_open = payload.get("next_open", 0)
        pair = payload.get("pair", "unknown")
        status = "OPEN" if market_open else "CLOSED"
        self.logger.info(f"[MarketStatus] {pair} market is {status}, next_open={next_open}")

    def _handle_connect(self, payload: dict[str, Any]) -> None:
        """Handle initial connection from platform."""
        self._platform_info = payload
        self._platform_connected = True
        self._last_heartbeat_time = time.time()
        self._disconnect_time = None
        self._last_disconnect_was_real = True
        platform = payload.get('platform', 'unknown')
        version = payload.get('version', 'unknown')
        pair = payload.get('pair', 'unknown')
        account = payload.get('account', 'N/A')
        self.logger.info(f"Platform connected: {platform} v{version} | Pair: {pair} | Account: {account}")
        for cb in self._connection_listeners:
            with contextlib.suppress(Exception):
                cb(True)

    def _handle_entry_fill(self, payload: dict[str, Any]) -> None:
        """Handle entry fill notification."""
        trade_id = payload.get('trade_id')
        entry_price = payload.get('entry_price')
        stop_loss = payload.get('stop_loss')
        take_profit = payload.get('take_profit')
        quantity = payload.get('quantity')
        account = payload.get('account')
        self.logger.info(
            f"Entry fill: {trade_id} @ {entry_price} qty={quantity} account={account} "
            f"SL={stop_loss} TP={take_profit}"
        )
        self._log_entry_latency(trade_id, entry_price)

    def _log_entry_latency(self, trade_id: str | None, entry_price: float | None) -> None:
        """Log the per-entry latency breakdown (queue -> wire -> ack -> fill) and slippage."""
        if not trade_id:
            return
        with self._lock:
            timing = self._entry_order_timing.pop(trade_id, None)
        if not timing:
            return
        now = time.time()
        queued = timing.get('queued')
        if queued is None:
            return
        wire_time = timing.get('wire_time')
        ack_time = timing.get('ack_time')
        parts = []
        if wire_time is not None:
            parts.append(f"queue->wire={(wire_time - queued) * 1000:.0f}ms")
        if ack_time is not None and wire_time is not None:
            parts.append(f"wire->ack={(ack_time - wire_time) * 1000:.0f}ms")
        if ack_time is not None:
            parts.append(f"ack->fill={(now - ack_time) * 1000:.0f}ms")
        parts.append(f"total={(now - queued) * 1000:.0f}ms")
        signal = timing.get('signal_price')
        slippage = ""
        if signal is not None and entry_price is not None:
            slippage = f" | signal={signal} fill={entry_price} slippage={entry_price - signal:+.2f}pts"
        direction = timing.get('direction', '?')
        self.logger.info(f"[LATENCY] entry {trade_id} ({direction}): {' '.join(parts)}{slippage}")

    def _handle_exit_fill(self, payload: dict[str, Any]) -> None:
        """Handle exit fill notification."""
        trade_id = payload.get('trade_id')
        exit_price = payload.get('exit_price')
        result_type = payload.get('result_type')
        realized_pnl = payload.get('realized_pnl')
        commission = payload.get('commission')
        account = payload.get('account')
        self.logger.info(
            f"Exit fill: {trade_id} @ {exit_price} ({result_type}) "
            f"pnl={realized_pnl} commission={commission} account={account}"
        )

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

            with self._lock:
                # Check if this was a pending command
                if seq_num in self._pending_commands:
                    cmd_info = self._pending_commands.pop(seq_num)
                    self._command_retries.pop(seq_num, None)
                    now = time.time()
                    elapsed = now - cmd_info.get('sent_time', 0)
                    wire_time = cmd_info.get('wire_time')
                    if wire_time is not None:
                        py_ms = (wire_time - cmd_info.get('sent_time', 0)) * 1000
                        nt_ms = (now - wire_time) * 1000
                        timing = f" ({elapsed:.2f}s | py={py_ms:.0f}ms nt={nt_ms:.0f}ms)"
                    else:
                        timing = f" ({elapsed:.2f}s)"

                    # Feed the per-entry latency record, if this ack belongs to one
                    if ack.trade_id and ack.trade_id in self._entry_order_timing:
                        rec = self._entry_order_timing[ack.trade_id]
                        rec['ack_time'] = now
                        if wire_time is not None:
                            rec['wire_time'] = wire_time

                    if ack.success:
                        self.logger.info(f"✅ Command ACK: {ack.command_type} seq={seq_num} trade={ack.trade_id}{timing}")
                    else:
                        self.logger.error(f"❌ Command FAILED: {ack.command_type} seq={seq_num} error='{ack.message}'{timing}")
                        self._notify_command_failed(
                            ack.command_type,
                            ack.trade_id or cmd_info.get("payload", {}).get("trade_id"),
                            seq_num,
                            ack.message or "NACK",
                        )
                else:
                    # Ack for unknown command (possibly duplicate detection or late ack)
                    status = "✅" if ack.success else "❌"
                    self.logger.debug(f"{status} Command ACK (unknown): {ack.command_type} seq={seq_num} trade={ack.trade_id}")

        except Exception as e:
            self.logger.error(f"Error handling command ack: {e}")

    def _notify_command_failed(
        self,
        command_type: str,
        trade_id: str | None,
        seq_num: int,
        reason: str,
    ) -> None:
        """Notify registered listeners that a command failed."""
        if trade_id is None:
            return
        for cb in list(self._command_failure_listeners):
            with contextlib.suppress(Exception):
                cb(command_type, trade_id, seq_num, reason)

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
        # Send pong response
        from .protocol import TestPongMessage
        pong = TestPongMessage(timestamp=time.time())
        self._send_command(pong.to_envelope(seq_num=self._next_seq()))

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
        """Register a callback for a specific message type.

        Idempotent: registering the same callback twice is a no-op, so a
        gateway stop/start cycle (which re-registers all handlers) never
        delivers a message more than once.
        """
        with self._lock:
            if msg_type not in self._callbacks:
                self._callbacks[msg_type] = []
            if callback not in self._callbacks[msg_type]:
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

    def on_command_dropped(self, callback: Callable[[str, dict[str, Any]], None]) -> None:
        """Register callback for permanently dropped commands (after max retries).

        Called with (command_type, command_info) when a command cannot be delivered.
        """
        self._on_command_dropped = callback

    def on_command_failed(self, callback: Callable[[str, str, int, str], None]) -> None:
        """Register callback for commands that were NACK'd or timed out.

        Called with (command_type, trade_id, seq_num, reason) when the platform
        negatively acknowledges a command or when no ACK arrives before the
        timeout expires.
        """
        self._command_failure_listeners.append(callback)

    def on_command_timeout(self, callback: Callable[[str, dict[str, Any], int], None]) -> None:
        """Register callback for commands whose ACK never arrived in time.

        Called with (command_type, payload, seq_num) for EVERY timed-out
        command — including trade_id-less infra commands (subscribe,
        refresh_request, audit) that ``on_command_failed`` deliberately drops.

        Idempotent: registering the same callback twice is a no-op, so a
        gateway stop/start cycle never notifies listeners more than once.
        """
        if callback not in self._command_timeout_listeners:
            self._command_timeout_listeners.append(callback)

    def on_connection_change(self, callback: Callable[[bool], None]) -> None:
        """Register callback for platform connection state changes.

        Called with True when platform connects (heartbeat or connect handshake)
        and False when heartbeat timeout occurs.

        Idempotent: registering the same callback twice is a no-op, so a
        gateway stop/start cycle never notifies listeners more than once.
        """
        if callback not in self._connection_listeners:
            self._connection_listeners.append(callback)

    def on_market_status(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register market status callback.

        Payload contains:
            - market_open: bool (True when market is currently open)
            - next_open: int (Unix timestamp of next session open)
            - pair: str (trading pair symbol)
        """
        self.on(MessageType.MARKET_STATUS, callback)

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

        with self._lock:
            # Track command for acknowledgment
            self._pending_commands[envelope.seq_num] = {
                'type': envelope.msg_type.value,
                'sent_time': time.time(),
                'payload': envelope.payload,
            }

            # Clean up old pending commands — throttle to at most once per second
            now = time.time()
            if now - self._last_cleanup_time >= 1.0:
                self._last_cleanup_time = now
                self._cleanup_pending_commands()

            # Warn if queue is at capacity (old commands will be silently dropped)
            if len(self._command_queue) >= self._command_queue.maxlen - 1:
                self.logger.critical(f"COMMAND QUEUE NEAR CAPACITY: {len(self._command_queue)}/{self._command_queue.maxlen} — old commands will be dropped!")

            self._command_queue.append(envelope)
            # Set the event while still holding the lock so the sender loop's
            # locked "queue empty?" check cannot clear a pending wakeup.
            self._command_ready.set()
        self.logger.debug(f"Queued: {envelope.msg_type.value} seq={envelope.seq_num}")

    def _cleanup_pending_commands(self) -> None:
        """Remove old pending commands that likely won't get acks."""
        now = time.time()
        timeout = self._command_ack_timeout_sec
        to_remove = [
            seq for seq, info in self._pending_commands.items()
            if now - info.get('sent_time', 0) > timeout
        ]
        seen_timeout_keys: set[tuple[str, str | None]] = set()
        for seq in to_remove:
            cmd_info = self._pending_commands.pop(seq)
            self._command_retries.pop(seq, None)
            self.logger.warning(f"Command timed out waiting for ack: {cmd_info['type']} seq={seq}")
            self._notify_command_failed(
                cmd_info['type'],
                cmd_info.get('payload', {}).get('trade_id'),
                seq,
                "timeout",
            )
            payload = cmd_info.get('payload', {})
            key = self._timeout_key(cmd_info['type'], payload)
            if key in seen_timeout_keys:
                continue
            seen_timeout_keys.add(key)
            for cb in list(self._command_timeout_listeners):
                with contextlib.suppress(Exception):
                    cb(cmd_info['type'], payload, seq)

        # Drop entry-latency records whose fill never arrived (e.g. rejected entry)
        stale_entries = [
            tid for tid, rec in self._entry_order_timing.items()
            if now - rec.get('queued', now) > timeout
        ]
        for tid in stale_entries:
            self._entry_order_timing.pop(tid, None)

    def _timeout_key(self, command_type: str, payload: Any) -> tuple[str, str | None]:
        """Dedupe command-timeout notifications within one cleanup sweep.

        For infra commands (subscribe, refresh) the instrument is used; for
        order commands the trade_id is used. This prevents a backlog of
        timed-out commands for the same instrument from machine-gunning the
        subscription supervisor.
        """
        if isinstance(payload, dict):
            return (command_type, payload.get('instrument') or payload.get('trade_id'))
        return (command_type, None)

    def _resolve_instrument(self, instrument: str | None) -> str:
        """Return the instrument for a command, or raise when absent.

        There is no gateway-level default instrument: every command must
        carry its instrument explicitly.
        """
        if not instrument:
            raise ValueError("instrument is required — commands must carry it explicitly")
        return instrument

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
        account: str | None = None,
        instrument: str | None = None,
    ) -> None:
        """Send open order command to platform."""
        if direction not in ("long", "short"):
            raise ValueError(f"Invalid order direction: {direction!r}. Expected 'long' or 'short'.")
        if not pair:
            raise ValueError("pair is required for open order commands")
        resolved_instrument = self._resolve_instrument(instrument)
        cmd = OpenOrderCommand(
            trade_id=trade_id,
            pair=pair,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_points=risk_points,
            rr_ratio=rr_ratio,
            instrument=resolved_instrument,
            risk_usd=risk_usd,
            risk_pct=risk_pct,
            account=account,
        )
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        with self._lock:
            info = self._pending_commands.get(envelope.seq_num, {})
            self._entry_order_timing[trade_id] = {
                'seq_num': envelope.seq_num,
                'queued': info.get('sent_time'),
                'signal_price': entry_price,
                'direction': direction,
            }
        self.logger.info(f"Queued OPEN order: {trade_id} {direction} {resolved_instrument} @ {entry_price} account={account}")

    def send_close_order(
        self,
        trade_id: str,
        reason: str | None = None,
        account: str | None = None,
        instrument: str | None = None,
    ) -> None:
        """Send close order command to platform."""
        resolved_instrument = self._resolve_instrument(instrument)
        cmd = CloseOrderCommand(
            trade_id=trade_id,
            instrument=resolved_instrument,
            reason=reason,
            account=account,
        )
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued CLOSE order: {trade_id} {resolved_instrument} (reason: {reason}) account={account}")

    def send_modify_order(
        self,
        trade_id: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        account: str | None = None,
        instrument: str | None = None,
    ) -> None:
        """Send modify order command to platform."""
        resolved_instrument = self._resolve_instrument(instrument)
        cmd = ModifyOrderCommand(
            trade_id=trade_id,
            instrument=resolved_instrument,
            stop_loss=stop_loss,
            take_profit=take_profit,
            account=account,
        )
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued MODIFY order: {trade_id} {resolved_instrument} SL={stop_loss} TP={take_profit} account={account}")

    def send_subscribe(self, instrument: str) -> None:
        """Tell the platform which instrument to use for live/historical data."""
        if not instrument:
            raise ValueError("instrument is required for subscribe commands")
        cmd = SubscribeMessage(instrument=instrument)
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued SUBSCRIBE command: {instrument}")

    def send_unsubscribe(self, instrument: str) -> None:
        """Tell the platform to stop streaming one instrument."""
        if not instrument:
            raise ValueError("instrument is required for unsubscribe commands")
        cmd = UnsubscribeMessage(instrument=instrument)
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued UNSUBSCRIBE command: {instrument}")

    def send_disconnect(self, reason: str = "stream stopped") -> None:
        """Tell the platform we are deliberately shutting the stream down.

        Best-effort: failures are logged and swallowed — the gateway may
        already be unhealthy when stop() runs.
        """
        try:
            cmd = DisconnectMessage(reason=reason)
            envelope = cmd.to_envelope(seq_num=self._next_seq())
            self._send_command(envelope)
            self.logger.info(f"Queued DISCONNECT command: {reason}")
        except Exception as e:
            self.logger.warning(f"Failed to send disconnect notification: {e}")

    def send_refresh_request(self, days: int = 1, instrument: str | None = None) -> None:
        """Request historical data refresh."""
        resolved = self._resolve_instrument(instrument)
        cmd = RefreshRequestMessage(days=days, instrument=resolved)
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued REFRESH request: {days} days, instrument={resolved}")

    def send_audit_request(self, bars_back: int = 60, instrument: str | None = None) -> None:
        """Request recent bars for verification (read-only audit)."""
        resolved = self._resolve_instrument(instrument)
        cmd = AuditRequestMessage(bars_back=bars_back, instrument=resolved)
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued AUDIT request: last {bars_back} bars, instrument={resolved}")

    def on_audit_response(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register callback for audit response.

        Payload contains:
            - pair: str
            - bars: list[dict]  # OHLCV bars
            - count: int
        """
        self.on(MessageType.AUDIT_RESPONSE, callback)

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

    # -------------------------------------------------------------------------
    # Public API - Queries
    # -------------------------------------------------------------------------

    def query_positions(self, timeout_ms: int = 5000) -> list[dict[str, Any]] | None:
        """Synchronously query current positions from platform.

        REQ sockets are strictly send->receive state machines. Concurrent calls
        will cause EFSM errors. This method uses a dedicated lock for safety.
        If a timeout occurs, the REQ socket is recreated to reset its state.
        """
        if not self._running or not self._query_req:
            return None

        with self._query_lock:
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
                self.logger.warning("Position query timeout — recreating REQ socket")
                # Recreate REQ socket to reset FSM state
                try:
                    old = self._query_req
                    self._query_req = self._context.socket(zmq.REQ)
                    self._query_req.connect(self.config.query_rep)
                    old.close(linger=0)
                except Exception as rec_err:
                    self.logger.error(f"Failed to recreate REQ socket: {rec_err}")
                return None
            except Exception as e:
                self.logger.error(f"Position query error: {e}")
                return None

    # -------------------------------------------------------------------------
    # Public API - Status
    # -------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Check if gateway has been started."""
        return self._running

    @property
    def is_connected(self) -> bool:
        """Check if platform is connected and healthy."""
        return self._running and self._platform_connected

    @property
    def was_last_disconnect_real(self) -> bool:
        """True if the last disconnect lasted long enough to warrant a history refresh."""
        return self._last_disconnect_was_real

    @property
    def platform_info(self) -> dict[str, Any] | None:
        """Get information about connected platform."""
        return self._platform_info
