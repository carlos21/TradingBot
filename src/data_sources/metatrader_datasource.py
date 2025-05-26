# src/data_sources/metatrader_datasource.py
import threading, json
from typing import Callable, Dict, List
from .combined_datasource import CombinedDataSource

class MetaTraderDataSource(CombinedDataSource):
    def __init__(self, pair: str, creds: dict, ws_url: str):
        self.pair   = pair
        self.creds  = creds
        self.ws_url = ws_url

    def load_historical_bars(self) -> List[Dict]:
        # call your MT REST/historical API
        return fetch_mt5_history(self.pair, timeframe="M1", **self.creds)

    def subscribe(self, callback: Callable[[Dict], None]) -> None:
        # spawn your MT5 / WebSocket client
        def _run():
            ws = connect_mt_ws(self.ws_url, **self.creds)
            # server should first PUSH your recent 1 m bars, then every tick
            while True:
                msg = ws.recv()         # dict with either bar‐keys or tick‐keys
                callback(msg)
        threading.Thread(target=_run, daemon=True).start()