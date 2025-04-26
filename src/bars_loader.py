from flask import abort
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser

import os
import csv
import threading


class BarsConfig:
    """Configuration for bar loading: time formats, timezones, CSV files, and initial date window."""
    DEFAULT_TIME_FMT = '%d/%m/%Y %H:%M:%S'
    DEFAULT_TIMEZONE = 'UTC'
    TIME_FORMATS = {
        'EURUSD': '%Y.%m.%d %H:%M',
        'NQ':      '%d/%m/%Y %H:%M:%S',
    }
    PAIR_TIMEZONES = {
        'EURUSD': 'Europe/London',
        'NQ':      'America/Chicago',
    }
    CSV_FILES = {
        'EURUSD': 'csvs/EURUSD_2024.csv',
        'NQ':      'csvs/NQ_2024.csv',
    }
    INITIAL_START = parser.parse("2024-09-01T00:00:00Z")
    INITIAL_END   = parser.parse("2024-10-02T02:20:59Z")


class BarsLoader:
    """Loads, aggregates, and streams bars for different trading pairs."""
    def __init__(self, config: BarsConfig, socketio, last_rows: int = None):
        self.config = config
        self.socketio = socketio
        self.all_1m_data = {
            pair: self.load_csv_data(filename, last_rows)
            for pair, filename in config.CSV_FILES.items()
        }
        self.current_agg_bars = []
        self.current_index = 0
        self.streaming = False
        self.stream_lock = threading.Lock()

    def load_csv_data(self, filename: str, last_rows: int = None, time_format: str = None):
        """
        Load CSV data for a given filename, parse timestamps with appropriate format and timezone,
        convert to UTC, and return sorted bar data.
        """
        pair = os.path.splitext(os.path.basename(filename))[0].split('_')[0]
        fmt = time_format or self.config.TIME_FORMATS.get(pair, self.config.DEFAULT_TIME_FMT)
        local_tz = ZoneInfo(self.config.PAIR_TIMEZONES.get(pair, self.config.DEFAULT_TIMEZONE))
        utc_tz = ZoneInfo('UTC')

        data = []
        with open(filename, 'r') as csvfile:
            reader = csv.DictReader(csvfile, delimiter=';')
            for row in reader:
                dt_naive = datetime.strptime(f"{row['Date']} {row['Time']}", fmt)
                dt_local = dt_naive.replace(tzinfo=local_tz)
                dt_utc = dt_local.astimezone(utc_tz)
                data.append({
                    'time':   int(dt_utc.timestamp()),
                    'open':   float(row['Open']),
                    'high':   float(row['High']),
                    'low':    float(row['Low']),
                    'close':  float(row['Close']),
                    'volume': int(row['Volume']),
                })
        data.sort(key=lambda b: b['time'])
        return data[-last_rows:] if last_rows else data

    def aggregate_bars(self, bars: list, group_size: int = 5):
        """
        Aggregate bars into larger intervals (e.g., 5-minute bars).
        """
        window = group_size * 60
        agg = []
        for i in range(0, len(bars), group_size):
            group = bars[i:i+group_size]
            if len(group) < group_size:
                break
            high   = max(b['high'] for b in group)
            low    = min(b['low'] for b in group)
            open_  = group[0]['open']
            close_ = group[-1]['close']
            vol    = sum(b['volume'] for b in group)
            last_ts = group[-1]['time']
            aligned = ((last_ts // window) + 1) * window
            agg.append({
                'time':   aligned,
                'open':   open_,
                'high':   high,
                'low':    low,
                'close':  close_,
                'volume': vol,
            })
        return agg

    def prepare_agg_bars(self, pair: str, tf: str, start_time: int = None):
        raw_1m = self.all_1m_data.get(pair)
        if raw_1m is None:
            abort(400, f"Unknown pair '{pair}'")

        # 1) aggregate to the requested TF
        unit    = tf[-1]
        num     = int(tf[:-1])
        minutes = num * (60 if unit == 'h' else 1)
        agg     = self.aggregate_bars(raw_1m, group_size=minutes)

        # 2) decide our cutoff
        if start_time is None:
            # first‐load: use your INITIAL_START / INITIAL_END window
            start_ts = int(self.config.INITIAL_START.timestamp())
            end_ts   = int(self.config.INITIAL_END.timestamp())
        else:
            # TF‐switch: show everything *up to* the client’s last seen bar
            start_ts = 0
            end_ts   = start_time

        # 3) filter accordingly
        initial = [b for b in agg if start_ts <= b['time'] <= end_ts]

        # 4) reset your streaming pointer
        self.current_agg_bars = agg
        # pick the first bar *after* end_ts
        self.current_index    = next(
            (i for i,b in enumerate(agg) if b['time'] > end_ts),
            len(agg)
        )

        return initial

    def stream_bars(self):
        """Background task to stream bars over WebSocket."""
        while self.streaming and self.current_index < len(self.current_agg_bars):
            with self.stream_lock:
                bar = self.current_agg_bars[self.current_index]
                self.current_index += 1
            self.socketio.emit('bar', bar)
            self.socketio.sleep(0.1)
        self.streaming = False