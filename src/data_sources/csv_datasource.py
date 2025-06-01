import csv, threading, time
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser
from typing import Callable, Dict, List
from .combined_datasource import CombinedDataSource

class CSVDataSource(CombinedDataSource):
    def __init__(self, pair: str, filename: str, time_fmt: str, tz: str):
        self.pair   = pair
        self.file   = filename
        self.fmt    = time_fmt
        self.local  = ZoneInfo(tz)
        self.utc    = ZoneInfo("UTC")
        self._bars = self._load_historical_bars()

    def _load_historical_bars(self) -> List[Dict]:
        bars = []
        with open(self.file, newline='') as f:
            sample  = f.read(2048); f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=";,")
            reader  = csv.DictReader(f, dialect=dialect)
            for r in reader:
                ts = f"{r['Date']} {r['Time']}"
                try:
                    dt = datetime.strptime(ts, self.fmt)
                except:
                    dt = parser.parse(ts)
                dt = dt.replace(tzinfo=self.local).astimezone(self.utc)
                bars.append({
                    "time":   int(dt.timestamp()),
                    "open":   float(r["Open"]),
                    "high":   float(r["High"]),
                    "low":    float(r["Low"]),
                    "close":  float(r["Close"]),
                    "volume": int(r.get("Volume", 0)),
                    "pair":   self.pair
                })
        return bars
    
    def load_historical_bars(self) -> List[Dict]:
        return list(self._bars)

    def subscribe(self, callback: Callable[[Dict], None], from_time: int = 0) -> None:
        """
        Replay all cached bars whose bar['time'] > from_time, sleeping between each
        bar according to (time difference) / speed.  Larger `speed` → faster replay.
        """
        def _replay():
            for bar in self._bars:
                if bar["time"] <= from_time:
                    continue
                callback(bar)

        threading.Thread(target=_replay, daemon=True).start()