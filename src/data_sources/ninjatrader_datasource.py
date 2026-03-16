import json
import socket
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from .combined_datasource import CombinedDataSource

logger = logging.getLogger(__name__)


@dataclass
class NinjaTraderConfig:
    host: str = "0.0.0.0"
    port: int = 8889
    pair: str = "NQ"
    heartbeat_timeout: float = 15.0


class NinjaTraderDataSource(CombinedDataSource):
    def __init__(self, cfg: NinjaTraderConfig):
        self._cfg = cfg
        self._historical_bars: List[Dict] = []
        self._stop_event = threading.Event()
        self._live = False
        self._server_socket: socket.socket = None

    def load_historical_bars(self, timeframe: str = '1m', start_time: int = None) -> List[Dict]:
        bars = self._historical_bars
        if start_time is not None:
            bars = [b for b in bars if b['time'] >= start_time]
        return bars

    def subscribe(self, callback: Callable[[Dict], None], from_time: int = 0) -> None:
        self._stop_event.clear()
        self._live = False

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._cfg.host, self._cfg.port))
        srv.listen(1)
        srv.settimeout(1.0)
        self._server_socket = srv
        logger.info(f"[NTDataSrc] Listening on {self._cfg.host}:{self._cfg.port}")

        try:
            while not self._stop_event.is_set():
                conn = self._accept_connection(srv)
                if conn is None:
                    continue
                self._handle_connection(conn, callback, from_time)
        finally:
            srv.close()
            self._server_socket = None
            logger.info("[NTDataSrc] Server shutdown")

    def pause(self):
        self._stop_event.set()

    def _accept_connection(self, srv: socket.socket) -> socket.socket:
        try:
            conn, addr = srv.accept()
            logger.info(f"[NTDataSrc] NT connected from {addr}")
            return conn
        except socket.timeout:
            return None

    def _handle_connection(self, conn: socket.socket, callback: Callable, from_time: int):
        conn.settimeout(self._cfg.heartbeat_timeout)
        buf = b""
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    logger.warning("[NTDataSrc] Heartbeat timeout, closing connection")
                    break
                if not chunk:
                    logger.warning("[NTDataSrc] NT disconnected")
                    break

                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode(errors="ignore").strip()
                    if not text:
                        continue
                    self._process_message(text, callback, from_time)
        finally:
            conn.close()

    def _process_message(self, text: str, callback: Callable, from_time: int):
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"[NTDataSrc] Invalid JSON: {text[:100]}")
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
            if bar["time"] > from_time:
                callback(bar)

        elif msg_type == "HISTORY_END":
            self._live = True
            logger.info(f"[NTDataSrc] History complete ({len(self._historical_bars)} bars), switching to live")

        elif msg_type == "TICK":
            tick = {
                "time": int(msg["time"]),
                "price": float(msg["price"]),
                "volume": int(msg.get("volume", 0)),
                "pair": msg.get("pair", self._cfg.pair),
            }
            callback(tick)
