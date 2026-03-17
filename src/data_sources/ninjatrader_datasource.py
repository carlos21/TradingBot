import json
import socket
import threading
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .combined_datasource import CombinedDataSource

logger = logging.getLogger(__name__)


@dataclass
class NinjaTraderConfig:
    host: str = "0.0.0.0"
    port: int = 8889
    pair: str = "NQ"
    heartbeat_timeout: float = 30.0


class NinjaTraderDataSource(CombinedDataSource):
    """
    TCP server that accepts a NinjaTrader connection immediately on creation.
    Messages are buffered in a queue until subscribe() hooks up the callback.
    Ticks are aggregated into 1m bars in real-time.
    """

    def __init__(self, cfg: NinjaTraderConfig):
        self._cfg = cfg
        self._historical_bars: List[Dict] = []
        self._live = False
        self._server_socket: Optional[socket.socket] = None

        # _stop_event: used by BarsLoader to unblock subscribe()/pause()
        self._stop_event = threading.Event()

        # _server_stop: controls the TCP server lifecycle (only set on shutdown)
        self._server_stop = threading.Event()

        # Callback set by subscribe(); guarded by _cb_lock
        self._callback: Optional[Callable[[Dict], None]] = None
        self._from_time: int = 0
        self._cb_lock = threading.Lock()

        # Buffer messages that arrive before subscribe()
        self._pending: deque = deque()

        # Tick-to-bar aggregation
        self._current_bar: Optional[Dict] = None
        self._last_emit_time: float = 0.0

        # Start TCP server immediately in a background thread
        self._server_thread = threading.Thread(target=self._run_server, daemon=True)
        self._server_thread.start()

    # ---- public interface ---------------------------------------------------

    def load_historical_bars(self, timeframe: str = '1m', start_time: int = None) -> List[Dict]:
        bars = self._historical_bars
        if start_time is not None:
            bars = [b for b in bars if b['time'] >= start_time]
        return bars

    def subscribe(self, callback: Callable[[Dict], None], from_time: int = 0) -> None:
        """
        Hook up the bar/tick callback.  Any messages that arrived before this
        call are replayed immediately, then future messages are forwarded live.
        """
        self._stop_event.clear()

        with self._cb_lock:
            self._callback = callback
            self._from_time = from_time

            # Drain anything that was buffered before subscribe()
            while self._pending:
                msg = self._pending.popleft()
                self._deliver(msg)

        # Block until pause() so BarsLoader's background task stays alive
        self._stop_event.wait()

    def pause(self):
        self._stop_event.set()

    def shutdown(self):
        """Stop the TCP server entirely."""
        self._server_stop.set()
        self._stop_event.set()

    # ---- TCP server (runs from __init__) ------------------------------------

    def _run_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._cfg.host, self._cfg.port))
        srv.listen(1)
        srv.settimeout(1.0)
        self._server_socket = srv
        print(f"[NTDataSrc] TCP server listening on {self._cfg.host}:{self._cfg.port}")

        try:
            while not self._server_stop.is_set():
                conn = self._accept_connection(srv)
                if conn is None:
                    continue
                try:
                    self._handle_connection(conn)
                except Exception as e:
                    print(f"[NTDataSrc] Connection handler error: {e}")
                print("[NTDataSrc] Connection closed, waiting for new connection...")
        finally:
            srv.close()
            self._server_socket = None
            print("[NTDataSrc] Server shutdown")

    def _accept_connection(self, srv: socket.socket) -> Optional[socket.socket]:
        try:
            conn, addr = srv.accept()
            print(f"[NTDataSrc] NinjaTrader connected from {addr}")
            return conn
        except socket.timeout:
            return None

    def _handle_connection(self, conn: socket.socket):
        conn.settimeout(self._cfg.heartbeat_timeout)
        buf = b""
        try:
            while not self._server_stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    print("[NTDataSrc] Heartbeat timeout, closing connection")
                    break
                except OSError as e:
                    print(f"[NTDataSrc] Socket error: {e}")
                    break
                if not chunk:
                    print("[NTDataSrc] NinjaTrader disconnected (empty recv)")
                    break

                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode(errors="ignore").strip().lstrip('\ufeff')
                    if not text:
                        continue
                    self._process_message(text)
        finally:
            conn.close()

    # ---- message processing -------------------------------------------------

    def _process_message(self, text: str):
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            print(f"[NTDataSrc] Invalid JSON: {text[:200]}")
            return

        msg_type = msg.get("type", "")

        if msg_type == "HEARTBEAT":
            return

        if msg_type == "BAR":
            bar = {
                "time": int(msg["time"]),
                "open": float(msg["open"]),
                "high": float(msg["high"]),
                "low": float(msg["low"]),
                "close": float(msg["close"]),
                "volume": int(msg.get("volume", 0)),
                "pair": msg.get("pair", self._cfg.pair),
            }
            if not self._live:
                self._historical_bars.append(bar)
            self._enqueue_or_deliver(bar)

        elif msg_type == "HISTORY_END":
            self._live = True
            print(f"[NTDataSrc] History complete ({len(self._historical_bars)} bars), switching to live")

        elif msg_type == "TICK":
            self._handle_tick(msg)

    def _handle_tick(self, msg: Dict):
        """Aggregate ticks into 1m bars."""
        tick_time = int(msg["time"])
        price = float(msg["price"])
        volume = int(msg.get("volume", 0))
        pair = msg.get("pair", self._cfg.pair)

        # Round down to the start of the current minute
        bar_time = (tick_time // 60) * 60

        if self._current_bar is not None and self._current_bar["time"] != bar_time:
            # Minute boundary crossed — close the current bar (completed)
            self._enqueue_or_deliver(self._current_bar)
            self._current_bar = None

        if self._current_bar is None:
            # Start a new bar
            self._current_bar = {
                "time": bar_time,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "pair": pair,
            }
        else:
            # Update current bar
            self._current_bar["high"] = max(self._current_bar["high"], price)
            self._current_bar["low"] = min(self._current_bar["low"], price)
            self._current_bar["close"] = price
            self._current_bar["volume"] += volume

        # Emit partial bar update at most once per second
        now = time.monotonic()
        if now - self._last_emit_time >= 1.0:
            self._last_emit_time = now
            partial = dict(self._current_bar)
            partial["partial"] = True
            self._enqueue_or_deliver(partial)

    def _enqueue_or_deliver(self, msg: Dict):
        with self._cb_lock:
            if self._callback is not None:
                self._deliver(msg)
            else:
                self._pending.append(msg)

    def _deliver(self, msg: Dict):
        """Forward a parsed bar/tick to the subscriber callback."""
        if "open" in msg:
            if msg["time"] > self._from_time:
                self._callback(msg)
        else:
            self._callback(msg)
