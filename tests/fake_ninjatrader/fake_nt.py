"""Fake NinjaTrader that speaks real ZeroMQ to the TradingGateway.

This class creates actual ZMQ sockets and connects to the Python-bound
addresses, making it indistinguishable from a real NinjaTrader connector
for the purpose of end-to-end testing.

Usage in a test::

    addresses = {
        "market_data": "tcp://127.0.0.1:5555",
        "commands":    "tcp://127.0.0.1:5556",
        "queries":     "tcp://127.0.0.1:5557",
        "heartbeat":   "tcp://127.0.0.1:5558",
    }
    nt = FakeNinjaTrader(addresses, accounts=["Sim101"], logger=logger)
    nt.start()
    nt.send_connect(pair="MNQ")
    nt.send_history_batch(bars)
    nt.send_history_end()
    # ... feed live bars ...
    cmd = nt.wait_for_command("order_open")
    nt.simulate_entry_fill(cmd["trade_id"], ...)
    nt.stop()
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Literal

import zmq

from src.infrastructure.gateway.protocol import (
    AuditResponseMessage,
    BarMessage,
    CommandAckMessage,
    ConnectMessage,
    EntryFillMessage,
    ExitFillMessage,
    HeartbeatMessage,
    HistoryBatchMessage,
    MarketStatusMessage,
    MessageEnvelope,
    MessageType,
    PositionSyncMessage,
    TickMessage,
    TradeLogMessage,
)
from src.utils.app_logger import ILogger

from .order_tracker import FakeOrderTracker


class FakeNinjaTrader:
    """Stateful fake NinjaTrader communicating over real ZMQ sockets."""

    def __init__(
        self,
        addresses: dict[str, str],
        accounts: list[str],
        logger: ILogger,
        auto_fill_entries: bool = False,
        auto_fill_exits: bool = False,
    ) -> None:
        self._addresses = addresses
        self._accounts = accounts
        self._logger = logger
        self._tracker = FakeOrderTracker(accounts)
        self._auto_fill_entries = auto_fill_entries
        self._auto_fill_exits = auto_fill_exits

        # ZMQ
        self._context: zmq.Context | None = None
        self._market_pub: zmq.Socket | None = None      # PUB → Python SUB
        self._command_pull: zmq.Socket | None = None    # PULL ← Python PUSH
        self._query_req: zmq.Socket | None = None       # REQ → Python REP
        self._heartbeat_pub: zmq.Socket | None = None   # PUB → Python SUB

        # Threads
        self._running = False
        self._threads: list[threading.Thread] = []

        # Sequencing
        self._seq_num = 0
        self._lock = threading.RLock()

        # Command recording (for test assertions)
        self._commands_received: deque[dict[str, Any]] = deque(maxlen=10000)
        self._command_condition = threading.Condition(self._lock)

        # Connection state
        self._connected = False

        # Audit: keep every bar streamed so we can respond to AUDIT_REQUEST
        self._audit_bars: deque[dict[str, Any]] = deque(maxlen=10000)

        # Auto-fill exit tracking: trade_id -> {direction, entry, stop_loss, take_profit}
        self._auto_filled_trades: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Create ZMQ sockets, connect to Python, and start background threads."""
        with self._lock:
            if self._running:
                return
            self._running = True

        self._context = zmq.Context()

        # PUB for market data / fills / acks / sync
        self._market_pub = self._context.socket(zmq.PUB)
        self._market_pub.setsockopt(zmq.SNDHWM, 5000)
        self._market_pub.connect(self._addresses["market_data"])

        # PULL for commands from Python
        self._command_pull = self._context.socket(zmq.PULL)
        self._command_pull.setsockopt(zmq.RCVHWM, 5000)
        self._command_pull.connect(self._addresses["commands"])

        # REQ for queries to Python
        self._query_req = self._context.socket(zmq.REQ)
        self._query_req.connect(self._addresses["queries"])

        # PUB for heartbeats
        self._heartbeat_pub = self._context.socket(zmq.PUB)
        self._heartbeat_pub.setsockopt(zmq.SNDHWM, 1000)
        self._heartbeat_pub.connect(self._addresses["heartbeat"])

        self._threads = [
            threading.Thread(target=self._command_loop, name="FakeNT-Command", daemon=True),
            threading.Thread(target=self._heartbeat_loop, name="FakeNT-Heartbeat", daemon=True),
        ]
        for t in self._threads:
            t.start()

        self._logger.info("FakeNinjaTrader started")

    def stop(self) -> None:
        """Shutdown threads and close ZMQ sockets."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        for t in self._threads:
            try:
                t.join(timeout=2.0)
            except Exception as e:
                self._logger.debug(f"Error joining thread: {e}")

        for sock in [self._market_pub, self._command_pull, self._query_req, self._heartbeat_pub]:
            if sock:
                try:
                    sock.close(linger=0)
                except Exception as e:
                    self._logger.debug(f"Error closing socket: {e}")

        if self._context:
            try:
                self._context.term()
            except Exception as e:
                self._logger.debug(f"Error terminating context: {e}")

        with self._lock:
            self._context = None
            self._threads = []
            self._commands_received.clear()
            self._seq_num = 0
            self._connected = False
            self._audit_bars.clear()
            self._auto_filled_trades.clear()

        self._logger.info("FakeNinjaTrader stopped")

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    def _command_loop(self) -> None:
        """Poll the PULL socket and process incoming commands."""
        while self._running:
            try:
                ready = self._command_pull.poll(timeout=100)
                if not ready:
                    continue

                json_msg = self._command_pull.recv_string()
                self._process_command(json_msg)
            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    break
            except Exception as e:
                self._logger.error(f"FakeNT command loop error: {e}")

    def _heartbeat_loop(self) -> None:
        """Send heartbeats every 5 seconds."""
        while self._running:
            try:
                self._send_heartbeat()
                # Sleep in small increments so we exit promptly
                for _ in range(50):
                    if not self._running:
                        break
                    time.sleep(0.1)
            except Exception as e:
                self._logger.error(f"FakeNT heartbeat loop error: {e}")

    # ------------------------------------------------------------------
    # Command processing
    # ------------------------------------------------------------------

    def _process_command(self, json_msg: str) -> None:
        envelope = MessageEnvelope.from_json(json_msg)
        payload = envelope.payload
        msg_type = envelope.msg_type
        seq_num = envelope.seq_num

        # Record for test introspection
        with self._lock:
            self._commands_received.append({
                "msg_type": msg_type.value,
                "seq_num": seq_num,
                "payload": payload,
            })
            self._command_condition.notify_all()

        # Duplicate detection
        if self._tracker.is_duplicate(seq_num):
            self._send_command_ack(
                command_type=msg_type.value,
                seq_num=seq_num,
                success=True,
                trade_id=payload.get("trade_id"),
                message="duplicate",
            )
            return

        self._tracker.mark_processed(seq_num)

        if msg_type == MessageType.ORDER_OPEN:
            self._handle_order_open(payload, seq_num)
        elif msg_type == MessageType.ORDER_CLOSE:
            self._handle_order_close(payload, seq_num)
        elif msg_type == MessageType.ORDER_MODIFY:
            self._handle_order_modify(payload, seq_num)
        elif msg_type == MessageType.REFRESH_REQUEST:
            self._handle_refresh_request(payload, seq_num)
        elif msg_type == MessageType.AUDIT_REQUEST:
            self._handle_audit_request(payload, seq_num)
        else:
            self._logger.warning(f"FakeNT: unhandled command {msg_type.value}")

    def _handle_order_open(self, payload: dict[str, Any], seq_num: int) -> None:
        trade_id = payload.get("trade_id", "")
        account = payload.get("account")
        direction = payload.get("direction", "long")
        entry_price = float(payload.get("entry_price", 0))
        stop_loss = float(payload.get("stop_loss", 0))
        take_profit = float(payload.get("take_profit", 0))

        ok, reason = self._tracker.track_entry(
            trade_id=trade_id,
            account=account,
            direction=direction,  # type: ignore[arg-type]
            quantity=1,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        self._send_command_ack(
            command_type="order_open",
            seq_num=seq_num,
            success=ok,
            trade_id=trade_id,
            message=reason or None,
        )

        if ok:
            self._send_trade_log(trade_id, "NT:ORDER", f"Entry order submitted: {direction} @ {entry_price}")

        if ok and self._auto_fill_entries:
            self.simulate_entry_fill(trade_id, entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit, account=account)
            if self._auto_fill_exits:
                with self._lock:
                    self._auto_filled_trades[trade_id] = {
                        "direction": direction,
                        "entry": entry_price,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "account": account,
                    }

    def _handle_order_close(self, payload: dict[str, Any], seq_num: int) -> None:
        trade_id = payload.get("trade_id", "")
        account = payload.get("account")

        ok, reason, entry = self._tracker.track_close(trade_id, account)

        self._send_command_ack(
            command_type="order_close",
            seq_num=seq_num,
            success=ok,
            trade_id=trade_id,
            message=reason or None,
        )

        if ok and entry:
            self._send_trade_log(trade_id, "NT:CLOSE", f"Close order submitted for {trade_id}")

    def _handle_order_modify(self, payload: dict[str, Any], seq_num: int) -> None:
        trade_id = payload.get("trade_id", "")
        account = payload.get("account")
        new_sl = payload.get("stop_loss")

        if new_sl is None:
            self._send_command_ack(
                command_type="order_modify",
                seq_num=seq_num,
                success=False,
                trade_id=trade_id,
                message="Missing stop_loss",
            )
            return

        ok, reason, entry = self._tracker.track_modify(trade_id, account, float(new_sl))

        self._send_command_ack(
            command_type="order_modify",
            seq_num=seq_num,
            success=ok,
            trade_id=trade_id,
            message=reason or None,
        )

        if ok and entry:
            self._send_trade_log(trade_id, "NT:MODIFY", f"Stop loss changed to {new_sl}")

    def _handle_refresh_request(self, payload: dict[str, Any], seq_num: int) -> None:
        # In a real scenario the test will manually send history after seeing this,
        # but we ack it immediately.
        self._send_command_ack(
            command_type="refresh_request",
            seq_num=seq_num,
            success=True,
        )

    def _handle_audit_request(self, payload: dict[str, Any], seq_num: int) -> None:
        """Handle AUDIT_REQUEST by returning the last N streamed bars."""
        bars_back = payload.get("bars_back", 60)
        try:
            bars_back = int(bars_back)
        except (ValueError, TypeError):
            bars_back = 60

        with self._lock:
            snapshot = list(self._audit_bars)
        recent = snapshot[-bars_back:] if len(snapshot) > bars_back else snapshot

        self._send_command_ack(
            command_type="audit_request",
            seq_num=seq_num,
            success=True,
        )

        msg = AuditResponseMessage(
            pair="MNQ",
            bars=recent,
            count=len(recent),
        )
        self._publish(msg.to_envelope(seq_num=self._next_seq()))
        self._logger.info(f"FakeNT: sent AUDIT_RESPONSE with {len(recent)} bars")

    # ------------------------------------------------------------------
    # Public API — Market data
    # ------------------------------------------------------------------

    def send_connect(self, pair: str = "MNQ") -> None:
        """Send the initial CONNECT handshake."""
        msg = ConnectMessage(platform="ninjatrader", version="2.0.0", pair=pair)
        self._publish(msg.to_envelope(seq_num=self._next_seq()))
        self._connected = True
        self._logger.info(f"FakeNT sent CONNECT for {pair}")

    def send_history_batch(self, bars: list[dict[str, Any]], days: int = 30) -> None:
        """Send a batch of historical bars."""
        msg = HistoryBatchMessage(pair="MNQ", bars=bars, days=days)
        self._publish(msg.to_envelope(seq_num=self._next_seq()))

    def send_history_end(self) -> None:
        """Signal end of historical data transmission."""
        envelope = MessageEnvelope.create(
            msg_type=MessageType.HISTORY_END,
            payload={},
            seq_num=self._next_seq(),
        )
        self._publish(envelope)

    def send_refresh_start(self) -> None:
        """Signal that the platform is about to send a fresh history batch."""
        envelope = MessageEnvelope.create(
            msg_type=MessageType.REFRESH_START,
            payload={},
            seq_num=self._next_seq(),
        )
        self._publish(envelope)

    def send_tick(self, tick: dict[str, Any]) -> None:
        """Send a single tick."""
        msg = TickMessage(
            pair=tick.get("pair", "MNQ"),
            price=float(tick["price"]),
            volume=int(tick.get("volume", 0)),
            time=int(tick["time"]),
            bid=tick.get("bid"),
            ask=tick.get("ask"),
        )
        self._publish(msg.to_envelope(seq_num=self._next_seq()))

    def send_bar(self, bar: dict[str, Any]) -> None:
        """Send a completed bar."""
        self._audit_bars.append(dict(bar))
        msg = BarMessage(
            pair=bar.get("pair", "MNQ"),
            time=int(bar["time"]),
            open=float(bar["open"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            close=float(bar["close"]),
            volume=int(bar.get("volume", 0)),
            is_partial=False,
        )
        self._publish(msg.to_envelope(seq_num=self._next_seq()))

    def send_partial_bar(self, bar: dict[str, Any]) -> None:
        """Send an in-progress (partial) bar."""
        msg = BarMessage(
            pair=bar.get("pair", "MNQ"),
            time=int(bar["time"]),
            open=float(bar["open"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            close=float(bar["close"]),
            volume=int(bar.get("volume", 0)),
            is_partial=True,
        )
        self._publish(msg.to_envelope(seq_num=self._next_seq()))

    def send_market_status(self, market_open: bool, next_open: int, pair: str = "MNQ") -> None:
        """Send market open/closed status."""
        msg = MarketStatusMessage(market_open=market_open, next_open=next_open, pair=pair)
        self._publish(msg.to_envelope(seq_num=self._next_seq()))

    def stream_bars(self, bars: list[dict[str, Any]], delay_sec: float = 0.0, batch_size: int = 0, batch_pause: float = 0.0) -> None:
        """Stream a sequence of bars over ZMQ, optionally with inter-bar delay.

        If *auto_fill_exits* is enabled, each bar is checked against tracked
        trades and EXIT_FILL is sent when TP or SL is hit.

        *batch_size* and *batch_pause* allow periodic pauses so the ZMQ
        subscriber can catch up and avoid dropped messages.
        """
        for idx, bar in enumerate(bars):
            if not self._running:
                break
            self.send_bar(bar)
            if self._auto_fill_exits:
                self._check_auto_fills(bar)
            if delay_sec > 0:
                time.sleep(delay_sec)
            if batch_size > 0 and batch_pause > 0 and (idx + 1) % batch_size == 0:
                time.sleep(batch_pause)

    def _check_auto_fills(self, bar: dict[str, Any]) -> None:
        """Check streamed bars against auto-filled trades and send EXIT_FILL on hits."""
        with self._lock:
            trades = dict(self._auto_filled_trades)

        for trade_id, spec in trades.items():
            direction = spec["direction"]
            sl = spec["stop_loss"]
            tp = spec["take_profit"]
            account = spec.get("account")
            high = float(bar.get("high", 0))
            low = float(bar.get("low", 0))

            if direction == "long":
                # Check SL first (conservative), then TP
                if low <= sl:
                    self.simulate_exit_fill(trade_id, exit_price=sl, result_type="SL", account=account)
                    with self._lock:
                        self._auto_filled_trades.pop(trade_id, None)
                    continue
                if high >= tp:
                    self.simulate_exit_fill(trade_id, exit_price=tp, result_type="TP", account=account)
                    with self._lock:
                        self._auto_filled_trades.pop(trade_id, None)
                    continue
            else:  # short
                # Check SL first (conservative), then TP
                if high >= sl:
                    self.simulate_exit_fill(trade_id, exit_price=sl, result_type="SL", account=account)
                    with self._lock:
                        self._auto_filled_trades.pop(trade_id, None)
                    continue
                if low <= tp:
                    self.simulate_exit_fill(trade_id, exit_price=tp, result_type="TP", account=account)
                    with self._lock:
                        self._auto_filled_trades.pop(trade_id, None)
                    continue

    # ------------------------------------------------------------------
    # Public API — Fills & logs
    # ------------------------------------------------------------------

    def simulate_entry_fill(
        self,
        trade_id: str,
        entry_price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        account: str | None = None,
    ) -> None:
        """Simulate an entry fill and send ENTRY_FILL to Python."""
        entry = self._tracker.fill_entry(trade_id)
        if entry is None:
            self._logger.warning(f"FakeNT: cannot fill entry for {trade_id} — not tracked or already filled")
            return

        sl = stop_loss if stop_loss is not None else entry.stop_loss
        tp = take_profit if take_profit is not None else entry.take_profit
        acct = account or entry.account

        msg = EntryFillMessage(
            trade_id=trade_id,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            account=acct,
        )
        self._publish(msg.to_envelope(seq_num=self._next_seq()))
        self._send_trade_log(trade_id, "NT:FILL", f"Entry fill @ {entry_price}")

    def simulate_exit_fill(
        self,
        trade_id: str,
        exit_price: float,
        result_type: Literal["TP", "SL", "CLOSE", "SP"],
        account: str | None = None,
    ) -> None:
        """Simulate an exit fill and send EXIT_FILL to Python."""
        entry = self._tracker.get_entry(trade_id)
        if entry is None:
            self._logger.warning(f"FakeNT: cannot send exit fill for {trade_id} — not tracked")
            return

        acct = account or entry.account
        msg = ExitFillMessage(
            trade_id=trade_id,
            exit_price=exit_price,
            result_type=result_type,  # type: ignore[arg-type]
            account=acct,
        )
        self._publish(msg.to_envelope(seq_num=self._next_seq()))
        self._send_trade_log(trade_id, "NT:FILL", f"Exit fill @ {exit_price} ({result_type})")

    def simulate_trade_log(self, trade_id: str, event: str, message: str) -> None:
        """Send a TRADE_LOG message to Python."""
        self._send_trade_log(trade_id, event, message)

    def send_audit_response(self, bars: list[dict[str, Any]]) -> None:
        """Manually send an AUDIT_RESPONSE (for direct test control)."""
        msg = AuditResponseMessage(
            pair="MNQ",
            bars=bars,
            count=len(bars),
        )
        self._publish(msg.to_envelope(seq_num=self._next_seq()))
        self._logger.info(f"FakeNT: manually sent AUDIT_RESPONSE with {len(bars)} bars")

    def send_position_sync(self) -> None:
        """Send POSITION_SYNC with current open positions."""
        positions = self._tracker.all_positions()
        msg = PositionSyncMessage(
            positions=positions,
            count=len(positions),
            source="ninjatrader",
            is_source_of_truth=True,
        )
        self._publish(msg.to_envelope(seq_num=self._next_seq()))

    def send_config_query(self, key: str = "accounts", timeout: float = 5.0) -> dict[str, Any]:
        """Send CONFIG_QUERY via REQ/REP and return the parsed response payload.

        Raises TimeoutError if no response arrives within ``timeout`` seconds.
        """
        if not self._query_req:
            raise RuntimeError("Query socket not initialized")

        envelope = MessageEnvelope.create(
            msg_type=MessageType.CONFIG_QUERY,
            payload={"key": key},
            seq_num=self._next_seq(),
        )
        self._query_req.send_string(envelope.to_json())

        # Use poll so we don't block forever on a misbehaving peer
        if self._query_req.poll(timeout=int(timeout * 1000)):
            resp_json = self._query_req.recv_string()
            resp_env = MessageEnvelope.from_json(resp_json)
            return resp_env.payload
        raise TimeoutError(f"CONFIG_QUERY timed out after {timeout}s")

    # ------------------------------------------------------------------
    # Public API — Test introspection
    # ------------------------------------------------------------------

    @property
    def commands_received(self) -> list[dict[str, Any]]:
        """Snapshot of all commands received so far."""
        with self._lock:
            return list(self._commands_received)

    def wait_for_command(
        self,
        msg_type: str,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Block until a command of the given type is received.

        Returns the command dict. Raises TimeoutError if not received.
        """
        deadline = time.time() + timeout
        with self._lock:
            while time.time() < deadline:
                for cmd in reversed(self._commands_received):
                    if cmd["msg_type"] == msg_type:
                        return cmd
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._command_condition.wait(timeout=min(0.05, remaining))
        raise TimeoutError(f"Timed out waiting for command '{msg_type}'")

    def wait_for_command_count(
        self,
        count: int,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        """Block until at least `count` commands have been received."""
        deadline = time.time() + timeout
        with self._lock:
            while len(self._commands_received) < count:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._command_condition.wait(timeout=min(0.05, remaining))
            if len(self._commands_received) < count:
                raise TimeoutError(f"Timed out waiting for {count} commands (got {len(self._commands_received)})")
            return list(self._commands_received)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        with self._lock:
            self._seq_num += 1
            return self._seq_num

    def _publish(self, envelope: MessageEnvelope) -> None:
        """Send an envelope via the market-data PUB socket."""
        if self._market_pub and self._running:
            try:
                self._market_pub.send_string(envelope.to_json())
            except Exception as e:
                self._logger.error(f"FakeNT publish error: {e}")

    def _send_heartbeat(self) -> None:
        """Send a heartbeat message."""
        if self._heartbeat_pub and self._running:
            msg = HeartbeatMessage(source="ninjatrader", status="ok")
            try:
                self._heartbeat_pub.send_string(msg.to_envelope(seq_num=self._next_seq()).to_json())
            except Exception as e:
                self._logger.error(f"FakeNT heartbeat error: {e}")

    def _send_command_ack(
        self,
        command_type: str,
        seq_num: int,
        success: bool,
        trade_id: str | None = None,
        message: str | None = None,
    ) -> None:
        msg = CommandAckMessage(
            command_type=command_type,
            seq_num=seq_num,
            success=success,
            trade_id=trade_id,
            message=message,
        )
        self._publish(msg.to_envelope(seq_num=self._next_seq()))

    def _send_trade_log(self, trade_id: str, event: str, message: str) -> None:
        msg = TradeLogMessage(trade_id=trade_id, event=event, message=message)
        self._publish(msg.to_envelope(seq_num=self._next_seq()))
