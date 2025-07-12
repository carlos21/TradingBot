import csv, threading
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser
from typing import Callable, Dict, List
from .combined_datasource import CombinedDataSource

class CSVDataSource(CombinedDataSource):

    DEFAULT_FMT    = '%d/%m/%Y %H:%M:%S'
    PAIR_FORMATS   = {
        'EURUSD': '%Y.%m.%d %H:%M',
        'NQ':      '%d/%m/%Y %H:%M:%S',
    }
    PAIR_TZS       = {
        'EURUSD': 'Europe/London',
        'NQ':      'America/Chicago',
    }
    PAIR_FILES     = {
        'EURUSD': 'csvs/EURUSD_2019.csv',
        'NQ':      'csvs/NQ_21-24.csv',
    }

    def __init__(
        self,
        pair: str,
        filename: str = None,
        time_fmt: str = None,
        tz: str = None
    ):
        self.pair   = pair
        self.fmt    = time_fmt or self.PAIR_FORMATS.get(pair, self.DEFAULT_FMT)
        self.local  = ZoneInfo(tz or self.PAIR_TZS.get(pair, 'UTC'))
        self.file   = filename or self.PAIR_FILES[pair]
        self.utc    = ZoneInfo("UTC")
        self._bars = self._load_historical_bars()

    def _load_historical_bars(self) -> List[Dict]:
        bars = []
        ny_tz = ZoneInfo("America/New_York")
        with open(self.file, newline='') as f:
            sample = f.read(2048)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=";,")
            reader = csv.DictReader(f, dialect=dialect)
            for r in reader:
                ts = f"{r['Date']} {r['Time']}"
                try:
                    dt = datetime.strptime(ts, self.fmt)
                except ValueError:
                    dt = parser.parse(ts)

                # Label as UTC, then convert to New York time
                dt_utc = dt.replace(tzinfo=self.utc)
                dt_ny = dt_utc.astimezone(ny_tz)

                bars.append({
                    "time":   int(dt_ny.timestamp()),
                    "open":   float(r["Open"]),
                    "high":   float(r["High"]),
                    "low":    float(r["Low"]),
                    "close":  float(r["Close"]),
                    "volume": int(r.get("Volume", 0)),
                    "pair":   self.pair,
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