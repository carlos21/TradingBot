import csv
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser
from src.data_sources.bars_datasource import BarsDataSource

import os


class CSVDataSource(BarsDataSource):
    def __init__(
        self,
        pair: str,
        filename: str,
        time_format: str,
        timezone: str,
        default_fmt: str = '%d/%m/%Y %H:%M:%S'
    ):
        self.pair        = pair
        self.filename    = filename
        self.time_format = time_format
        self.default_fmt = default_fmt
        self.local_tz    = ZoneInfo(timezone)
        self.utc_tz      = ZoneInfo('UTC')

    def load_1m_bars(self):
        data = []
        with open(self.filename, 'r', newline='') as f:
            sample  = f.read(2048); f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=";,")
            reader  = csv.DictReader(f, dialect=dialect)
            for row in reader:
                ts_str = f"{row.get('Date','')} {row.get('Time','')}".strip()
                try:
                    dt = datetime.strptime(ts_str, self.time_format)
                except Exception:
                    dt = parser.parse(ts_str)
                dt = dt.replace(tzinfo=self.local_tz).astimezone(self.utc_tz)
                data.append({
                    'time':   int(dt.timestamp()),
                    'open':   float(row['Open']),
                    'high':   float(row['High']),
                    'low':    float(row['Low']),
                    'close':  float(row['Close']),
                    'volume': int(row.get('Volume', 0)),
                    'pair':   self.pair,
                })
        return sorted(data, key=lambda b: b['time'])