from typing import Callable, Dict, List
from .combined_datasource import CombinedDataSource
from datetime import datetime, timedelta
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5
import socket
import time


TF_MAP = {
    '1m':  mt5.TIMEFRAME_M1,
    '5m':  mt5.TIMEFRAME_M5,
    '15m': mt5.TIMEFRAME_M15,
    '1h':  mt5.TIMEFRAME_H1,
    '4h':  mt5.TIMEFRAME_H4,
}

@dataclass
class MetaTraderConfig:
    login:     int
    password:  str
    server:    str
    history_days: int
    host:      str = "0.0.0.0"
    port:      int = 8888
    server_timezone:   str = 'Etc/GMT-3'
    exchange_timezone: str = 'America/Chicago'


class MetaTraderDataSource(CombinedDataSource):
    def __init__(self, symbol: str, cfg: MetaTraderConfig):
        self.symbol      = symbol
        self.creds     = dict(login=cfg.login, password=cfg.password, server=cfg.server)
        self._history_days = cfg.history_days
        self._host     = cfg.host
        self._port     = cfg.port
        self._tz_server   = ZoneInfo(cfg.server_timezone)
        self._tz_exchange = ZoneInfo(cfg.exchange_timezone)

        now_srv = datetime.now(self._tz_server)
        now_exc = now_srv.astimezone(self._tz_exchange)
        diff    = now_srv.utcoffset() - now_exc.utcoffset()
        self._offset_seconds = int(diff.total_seconds())

        if not mt5.initialize(**self.creds):
            raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

    def load_historical_bars(self, timeframe: str = '1m') -> List[Dict]:
        tf_const = TF_MAP.get(timeframe)
        if tf_const is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        if not mt5.symbol_select(self.symbol, True):
            print(f"⚠️ symbol_select failed for {self.symbol}: {mt5.last_error()}")
            return []

        utc_to   = datetime.utcnow()
        utc_from = utc_to - timedelta(days=self._history_days)
        rates    = mt5.copy_rates_range(self.symbol, tf_const, utc_from, utc_to)

        if rates is None or len(rates) == 0:
            print(f"⚠️ no history for {self.symbol}@{timeframe}, error={mt5.last_error()}")
            return []

        bars: List[Dict] = []
        for r in rates:
            t_server = int(r["time"])
            dt_srv = datetime.fromtimestamp(t_server, tz=self._tz_server)
            dt_exc = dt_srv.astimezone(self._tz_exchange)
            offset_seconds = int(
                (dt_srv.utcoffset() - dt_exc.utcoffset()).total_seconds()
            )
            t_exc = t_server - offset_seconds

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

    def subscribe(self, callback: Callable[[Dict], None], from_time: int = 0) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._host, self._port))
        srv.listen(1)
        print(f"[MTDataSrc] Listening on {self._host}:{self._port} for MQL5 ticks")

        conn, addr = srv.accept()
        print(f"[MTDataSrc] MQL5 EA connected from {addr}")
        buf = b""
        try:
            while True:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                buf += chunk
                # split on newline
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode(errors="ignore").strip()
                    if not text:
                        continue
                    parts = text.split()
                    try:
                        symbol = parts[0]
                        price  = float(parts[1])
                    except Exception as e:
                        print("[MTDataSrc] parse error:", e, "line:", text)
                        continue

                    tick = {
                        "time":   int(datetime.utcnow().timestamp()),
                        "price":  price,
                        "volume": 0,
                        "pair":   symbol
                    }
                    callback(tick)
        finally:
            conn.close()
            srv.close()
            print("[MTDataSrc] MQL5 EA disconnected, server closed")