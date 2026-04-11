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
import zmq
import json
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable, List, Union
from collections import deque
from concurrent.futures import Future

from .protocol import (
    MessageType,
    MessageEnvelope,
    TickMessage,
    BarMessage,
    HistoryBatchMessage,
    OpenOrderCommand,
    CloseOrderCommand,
    ModifyOrderCommand,
    EntryFillMessage,
    ExitFillMessage,
    TradeLogMessage,
    HeartbeatMessage,
    ConnectMessage,
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
    
    def get_platform_addresses(self) -> Dict[str, str]:
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
        gateway.on_tick(lambda tick: print(f"Price: {tick.price}"))
        
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
        config: Optional[GatewayConfig] = None,
        pair: str = "NQ",
        logger: Optional[ILogger] = None,
    ):
        self.config = config or GatewayConfig()
        self.pair = pair
        self.logger = logger or ConsoleLogger()
        
        # ZMQ context and sockets
        self._context: Optional[zmq.Context] = None
        self._market_sub: Optional[zmq.Socket] = None  # SUB: Receive market data
        self._command_push: Optional[zmq.Socket] = None  # PUSH: Send commands
        self._query_req: Optional[zmq.Socket] = None  # REQ: Sync queries
        self._heartbeat_sub: Optional[zmq.Socket] = None  # SUB: Receive heartbeats
        
        # Threading
        self._running = False
        self._threads: List[threading.Thread] = []
        self._lock = threading.RLock()
        self._seq_num = 0
        
        # Message queues for thread safety
        self._command_queue: deque = deque(maxlen=self.config.max_queue_size)
        self._outbound_queue: deque = deque(maxlen=self.config.max_queue_size)
        
        # Callbacks
        self._callbacks: Dict[MessageType, List[Callable]] = {
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
        }
        
        # Connection state
        self._platform_connected = False
        self._last_heartbeat_time: Optional[float] = None
        self._platform_info: Optional[Dict[str, Any]] = None
        
        # Pending commands for acknowledgment tracking
        self._pending_commands: Dict[str, Future] = {}
    
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
        ]
        
        for t in self._threads:
            t.start()
        
        self.logger.info(f"TradingGateway started. Listening on:")
        self.logger.info(f"  - Market data: {self.config.market_data_pub}")
        self.logger.info(f"  - Commands: {self.config.command_pull}")
        self.logger.info(f"  - Queries: {self.config.query_rep}")
    
    def stop(self) -> None:
        """Stop the gateway and cleanup resources."""
        if not self._running:
            return
            
        self.logger.info("Stopping TradingGateway...")
        self._running = False
        
        # Close sockets to unblock threads
        for socket in [self._market_sub, self._command_push, self._query_req, 
                       self._heartbeat_sub]:
            if socket:
                try:
                    socket.close(linger=0)
                except Exception as e:
                    self.logger.debug(f"Error closing socket: {e}")
        
        # Wait for threads
        for t in self._threads:
            try:
                t.join(timeout=2.0)
            except Exception as e:
                self.logger.debug(f"Error joining thread: {e}")
        
        # Terminate context
        if self._context:
            try:
                self._context.term()
            except Exception as e:
                self.logger.debug(f"Error terminating context: {e}")
        
        self._context = None
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
        
        # REQ socket: Sync queries (Python acts as client)
        self._query_req = self._context.socket(zmq.REQ)
        self._query_req.bind(self.config.query_rep)
        
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
        
        # REQ socket: Connect to platform's query REP
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
        
        while self._running:
            try:
                # Poll with timeout to allow checking _running
                ready = poller.poll(timeout=100)
                if not ready:
                    continue
                
                # Check market data socket
                if self._market_sub in dict(ready):
                    msg = self._market_sub.recv_string()
                    self._handle_message(msg)
                
                # Check heartbeat socket
                if self._heartbeat_sub in dict(ready):
                    msg = self._heartbeat_sub.recv_string()
                    self._handle_heartbeat(msg)
                    
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
    
    def _heartbeat_loop(self) -> None:
        """Background thread: Send heartbeats and check platform health."""
        while self._running:
            try:
                # Send heartbeat
                hb = HeartbeatMessage(source="python", status="ok")
                envelope = hb.to_envelope(seq_num=self._next_seq())
                
                if self._heartbeat_push:
                    self._heartbeat_push.send_string(envelope.to_json())
                
                # Check platform health
                if self._last_heartbeat_time:
                    elapsed = time.time() - self._last_heartbeat_time
                    if elapsed > self.config.heartbeat_timeout_sec:
                        if self._platform_connected:
                            self.logger.warning(f"Platform heartbeat timeout ({elapsed:.1f}s)")
                            self._platform_connected = False
                
                time.sleep(self.config.heartbeat_interval_sec)
                
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
            
            # Update sequence tracking
            self._seq_num = max(self._seq_num, envelope.seq_num)
            
            # Dispatch to callbacks
            callbacks = self._callbacks.get(envelope.msg_type, [])
            for callback in callbacks:
                try:
                    callback(envelope.payload)
                except Exception as e:
                    self.logger.error(f"Callback error for {envelope.msg_type}: {e}")
            
            # Special handling for certain message types
            if envelope.msg_type == MessageType.CONNECT:
                self._handle_connect(envelope.payload)
            elif envelope.msg_type == MessageType.ENTRY_FILL:
                self._handle_entry_fill(envelope.payload)
            elif envelope.msg_type == MessageType.EXIT_FILL:
                self._handle_exit_fill(envelope.payload)
                
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON received: {e}")
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
    
    def _handle_heartbeat(self, json_msg: str) -> None:
        """Process heartbeat from platform."""
        try:
            envelope = MessageEnvelope.from_json(json_msg)
            if envelope.msg_type == MessageType.HEARTBEAT:
                self._last_heartbeat_time = time.time()
                if not self._platform_connected:
                    self.logger.info("Platform connected (heartbeat received)")
                    self._platform_connected = True
        except Exception as e:
            self.logger.debug(f"Error handling heartbeat: {e}")
    
    def _handle_connect(self, payload: Dict[str, Any]) -> None:
        """Handle initial connection from platform."""
        self._platform_info = payload
        self._platform_connected = True
        self._last_heartbeat_time = time.time()
        self.logger.info(f"Platform connected: {payload.get('platform')} v{payload.get('version')}")
    
    def _handle_entry_fill(self, payload: Dict[str, Any]) -> None:
        """Handle entry fill notification."""
        self.logger.info(f"Entry fill: {payload.get('trade_id')} @ {payload.get('entry_price')}")
    
    def _handle_exit_fill(self, payload: Dict[str, Any]) -> None:
        """Handle exit fill notification."""
        self.logger.info(f"Exit fill: {payload.get('trade_id')} @ {payload.get('exit_price')} ({payload.get('result_type')})")
    
    # -------------------------------------------------------------------------
    # Public API - Callback Registration
    # -------------------------------------------------------------------------
    
    def on(self, msg_type: MessageType, callback: Callable) -> None:
        """Register a callback for a specific message type."""
        with self._lock:
            if msg_type not in self._callbacks:
                self._callbacks[msg_type] = []
            self._callbacks[msg_type].append(callback)
    
    def on_tick(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register tick callback."""
        self.on(MessageType.TICK, callback)
    
    def on_bar(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register bar callback."""
        self.on(MessageType.BAR, callback)
    
    def on_partial_bar(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register partial bar callback."""
        self.on(MessageType.PARTIAL_BAR, callback)
    
    def on_history_batch(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register history batch callback."""
        self.on(MessageType.HISTORY_BATCH, callback)
    
    def on_history_end(self, callback: Callable[[], None]) -> None:
        """Register history end callback."""
        self.on(MessageType.HISTORY_END, lambda _: callback())
    
    def on_entry_fill(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register entry fill callback."""
        self.on(MessageType.ENTRY_FILL, callback)
    
    def on_exit_fill(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register exit fill callback."""
        self.on(MessageType.EXIT_FILL, callback)
    
    def on_trade_log(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register trade log callback."""
        self.on(MessageType.TRADE_LOG, callback)
    
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
            self.logger.warning("Cannot send command: gateway not running")
            return
        self._command_queue.append(envelope)
    
    def send_open_order(
        self,
        trade_id: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_points: float,
        rr_ratio: float,
        pair: Optional[str] = None,
        risk_usd: Optional[float] = None,
        risk_pct: Optional[float] = None,
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
    
    def send_close_order(self, trade_id: str, reason: Optional[str] = None) -> None:
        """Send close order command to platform."""
        cmd = CloseOrderCommand(trade_id=trade_id, reason=reason)
        envelope = cmd.to_envelope(seq_num=self._next_seq())
        self._send_command(envelope)
        self.logger.info(f"Queued CLOSE order: {trade_id} (reason: {reason})")
    
    def send_modify_order(
        self,
        trade_id: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
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
    
    # -------------------------------------------------------------------------
    # Public API - Queries
    # -------------------------------------------------------------------------
    
    def query_positions(self, timeout_ms: int = 5000) -> Optional[List[Dict[str, Any]]]:
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
    def platform_info(self) -> Optional[Dict[str, Any]]:
        """Get information about connected platform."""
        return self._platform_info
