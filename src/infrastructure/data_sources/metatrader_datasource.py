import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5

from .combined_datasource import CombinedDataSource

logger = logging.getLogger(__name__)

# Map of timeframe strings to MT5 constants
TF_MAP = {
    '1m':  mt5.TIMEFRAME_M1,
    '5m':  mt5.TIMEFRAME_M5,
    '15m': mt5.TIMEFRAME_M15,
    '1h':  mt5.TIMEFRAME_H1,
    '4h':  mt5.TIMEFRAME_H4,
}

@dataclass
class MetaTraderConfig:
    login:            int
    password:         str
    server:           str
    history_days:     int
    host:             str = "0.0.0.0"  # nosec B104
    port:             int = 8888
    server_timezone:   str = 'Etc/GMT-3'
    exchange_timezone: str = 'America/Chicago'

class MetaTraderDataSource(CombinedDataSource):
    def __init__(self, symbol: str, cfg: MetaTraderConfig):
        self.symbol       = symbol
        self.pair         = symbol
        self.creds        = {'login': cfg.login, 'password': cfg.password, 'server': cfg.server}
        self._history_days = cfg.history_days
        self._host        = cfg.host
        self._port        = cfg.port
        self._tz_server   = ZoneInfo(cfg.server_timezone)
        self._tz_exchange = ZoneInfo(cfg.exchange_timezone)

        # compute offset between server and exchange TZs
        now_srv = datetime.now(self._tz_server)
        now_exc = now_srv.astimezone(self._tz_exchange)
        diff    = now_srv.utcoffset() - now_exc.utcoffset()
        self._offset_seconds = int(diff.total_seconds())

        # initialize MT5
        if not mt5.initialize(**self.creds):
            raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

    def load_historical_bars(self, timeframe: str = '1m') -> list[dict]:
        tf_const = TF_MAP.get(timeframe)
        if tf_const is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        # ensure symbol is selected
        if not mt5.symbol_select(self.symbol, True):
            logger.warning(f"symbol_select failed for {self.symbol}: {mt5.last_error()}")
            return []

        utc_to   = datetime.now(timezone.utc)
        utc_from = utc_to - timedelta(days=self._history_days)
        rates    = mt5.copy_rates_range(self.symbol, tf_const, utc_from, utc_to)

        if rates is None or len(rates) == 0:
            logger.warning(f"no history for {self.symbol}@{timeframe}, error={mt5.last_error()}")
            return []

        bars: list[dict] = []
        for r in rates:
            t_server = int(r["time"])
            dt_srv   = datetime.fromtimestamp(t_server, tz=self._tz_server)
            dt_exc   = dt_srv.astimezone(self._tz_exchange)
            offset   = int((dt_srv.utcoffset() - dt_exc.utcoffset()).total_seconds())
            t_exc    = t_server - offset

            bars.append({
                "time":   t_exc,
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": int(r["tick_volume"]),
                "pair":   self.symbol
            })

        return bars

    def pause(self) -> None:
        """MetaTrader streams over TCP; pausing is handled by the caller."""

    def subscribe(self,
                  callback: Callable[[dict], None],
                  from_time: int = 0) -> None:
        """
        1) Replay all historical 1m bars whose 'time' > from_time via callback(bar).
        2) Then open a TCP socket on (host,port), accept one EA connection,
           and stream every incoming tick line "<SYMBOL> <PRICE>\\n" as callback(tick).
        """
        # --- 1) replay history ---
        try:
            history = self.load_historical_bars('1m')
            for bar in history:
                if bar["time"] > from_time:
                    callback(bar)
        except Exception as e:
            logger.error(f"[MTDataSrc] error replaying history: {e}")

        # --- 2) live ticks over TCP ---
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._host, self._port))
        srv.listen(1)
        logger.info(f"[MTDataSrc] Listening for ticks on {self._host}:{self._port}…")

        conn = None
        try:
            conn, addr = srv.accept()
            logger.info(f"[MTDataSrc] EA connected from {addr}")
            buf = b""
            while True:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode(errors="ignore").strip()
                    if not text:
                        continue
                    parts = text.split()
                    if len(parts) != 2:
                        logger.warning(f"[MTDataSrc] malformed tick: '{text}'")
                        continue
                    symbol, price_str = parts
                    try:
                        price = float(price_str)
                    except ValueError:
                        logger.warning(f"[MTDataSrc] invalid price in tick: '{text}'")
                        continue

                    tick = {
                        "time":   int(datetime.now(timezone.utc).timestamp()),
                        "price":  price,
                        "volume": 0,
                        "pair":   symbol
                    }
                    callback(tick)
        finally:
            if conn is not None:
                conn.close()
            srv.close()
            logger.info("[MTDataSrc] Tick stream closed, server shutdown")
