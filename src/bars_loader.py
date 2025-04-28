from flask import abort
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser
import os
import csv
import threading


class BarsConfig:
    """Configuration for bar loading: time formats, timezones, CSV files, and initial date window."""
    DEFAULT_TIME_FMT    = '%d/%m/%Y %H:%M:%S'
    DEFAULT_TIMEZONE    = 'UTC'
    TIME_FORMATS        = {
        'EURUSD': '%Y.%m.%d %H:%M',
        'NQ':      '%d/%m/%Y %H:%M:%S',
    }
    PAIR_TIMEZONES      = {
        'EURUSD': 'Europe/London',
        'NQ':      'America/Chicago',
    }
    CSV_FILES           = {
        'EURUSD': 'csvs/EURUSD_2024.csv',
        'NQ':      'csvs/NQ_2024.csv',
    }
    INITIAL_START       = parser.parse("2024-09-01T00:00:00Z")
    INITIAL_END         = parser.parse("2024-10-02T02:20:59Z")

    # strategy parameters
    STOP_LOSS_CONFIG    = {
        'EURUSD': 0.0004,  # 4 pips
        'NQ':      10       # 10 points
    }
    MAX_BOUNCE_CONFIG   = {
        'EURUSD': 0.0020,   # 20 pips
        'NQ':      50       # 50 points
    }


class BarsLoader:
    """
    Loads, aggregates, and streams bars for different trading pairs.
    Always drives the 5m-based strategy and emits a single 'bar' stream for the user-selected timeframe,
    by re-aggregating 5m bars on the fly.
    """
    def __init__(self, config: BarsConfig, socketio, strategy=None, last_rows: int = None):
        self.config      = config
        self.socketio    = socketio
        self.strategy    = strategy
        self.stream_lock = threading.Lock()

        # load raw 1m data
        self.all_1m_data = {
            pair: self.load_csv_data(filename, last_rows)
            for pair, filename in self.config.CSV_FILES.items()
        }
        # build 5m base aggregates
        self.agg_5m = {}
        for pair, raw in self.all_1m_data.items():
            self.agg_5m[pair] = self.aggregate_bars(raw, group_size=5)
        # pointers
        self.current_5m_index = { pair: 0 for pair in self.agg_5m }
        self.streaming_5m     = { pair: False for pair in self.agg_5m }

        # dynamic timeframe settings
        self.current_tf   = '5m'
        self.tf_group     = 1        # number of 5m bars per emitted TF bar
        self.tf_buffer    = { pair: [] for pair in self.agg_5m }

    def set_timeframe(self, tf: str):
        """
        Set user-selected timeframe (multiple of 5m) and reset aggregation buffer.
        """
        self.current_tf = tf
        unit   = tf[-1]
        num    = int(tf[:-1])
        minutes = num * (60 if unit == 'h' else 1)
        self.tf_group = minutes // 5
        for pair in self.tf_buffer:
            self.tf_buffer[pair].clear()

    def load_csv_data(self, filename: str, last_rows: int = None, time_format: str = None):
        pair     = os.path.splitext(os.path.basename(filename))[0].split('_')[0]
        fmt      = time_format or self.config.TIME_FORMATS.get(pair, self.config.DEFAULT_TIME_FMT)
        local_tz = ZoneInfo(self.config.PAIR_TIMEZONES.get(pair, self.config.DEFAULT_TIMEZONE))
        utc_tz   = ZoneInfo('UTC')
        data = []
        with open(filename, 'r') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                dt = datetime.strptime(f"{row['Date']} {row['Time']}", fmt)
                dt = dt.replace(tzinfo=local_tz).astimezone(utc_tz)
                data.append({
                    'time':  int(dt.timestamp()),
                    'open':  float(row['Open']),
                    'high':  float(row['High']),
                    'low':   float(row['Low']),
                    'close': float(row['Close']),
                    'volume':int(row['Volume']),
                    'pair':  pair
                })
        data.sort(key=lambda b: b['time'])
        return data[-last_rows:] if last_rows else data

    def aggregate_bars(self, bars: list, group_size: int = 5):
        """
        Aggregate consecutive bars into larger intervals.
        Dynamically infers the base interval from the first two bars (in seconds)
        so that raw 1m (60s) or 5m (300s) data can be correctly re-grouped.
        """
        if len(bars) < 2:
            base_interval = 60
        else:
            # time difference between first two bars
            base_interval = bars[1]['time'] - bars[0]['time']
        window = group_size * base_interval
        agg = []
        for i in range(0, len(bars), group_size):
            chunk = bars[i:i+group_size]
            if len(chunk) < group_size:
                break
            pair    = chunk[0]['pair']
            high    = max(b['high']  for b in chunk)
            low     = min(b['low']   for b in chunk)
            open_   = chunk[0]['open']
            close_  = chunk[-1]['close']
            volume  = sum(b['volume'] for b in chunk)
            ts_last = chunk[-1]['time']
            aligned = ((ts_last // window) + 1) * window
            agg.append({
                'time':   aligned,
                'open':   open_,
                'high':   high,
                'low':    low,
                'close':  close_,
                'volume': volume,
                'pair':   pair
            })
        return agg

    def prepare_agg_bars(self, pair: str, tf: str, start_time: int = None):
        # re-aggregate 5m into tf bars for history requests
        self.set_timeframe(tf)
        base = self.agg_5m.get(pair)
        if base is None:
            abort(400, f"Unknown pair '{pair}'")
        # reuse aggregate_bars on the base array
        group  = self.tf_group
        agg_tf = self.aggregate_bars(base, group_size=group)
        if start_time is None:
            start_ts = int(self.config.INITIAL_START.timestamp())
            end_ts   = int(self.config.INITIAL_END.timestamp())
        else:
            start_ts, end_ts = 0, start_time
        initial = [b for b in agg_tf if start_ts <= b['time'] <= end_ts]
        # reset streaming pointer
        self.current_agg_bars = agg_tf
        self.current_index    = next((i for i,b in enumerate(agg_tf) if b['time'] > end_ts), len(agg_tf))
        return initial

    def stream_5m_bars(self, pair: str):
        """Continuously stream 5m bars, drive strategy, and emit grouped TF bars."""
        print(f"[BarsLoader] ▶▶ START stream_5m_bars for {pair}, starting idx={self.current_5m_index[pair]}")
        self.streaming_5m[pair] = True

        while self.streaming_5m[pair] and self.current_5m_index[pair] < len(self.agg_5m[pair]):
            with self.stream_lock:
                bar5 = self.agg_5m[pair][self.current_5m_index[pair]]
                idx_before = self.current_5m_index[pair]
                self.current_5m_index[pair] += 1

            print(f"[BarsLoader] processing 5m bar idx={idx_before}, time={bar5['time']}")

            # strategy logic on true 5m
            if self.strategy:
                try:
                    print(f"[BarsLoader] passing bar to strategy: {{'time':{bar5['time']}}}")
                    self.strategy.on_new_bar(bar5)
                except Exception as e:
                    print(f"[BarsLoader] ERROR in strategy.on_new_bar: {e}", flush=True)
                    import traceback; traceback.print_exc()

            # accumulate for tf grouping
            buf = self.tf_buffer[pair]
            buf.append(bar5)
            print(f"[BarsLoader] buffer size now {len(buf)}/{self.tf_group}")
            if len(buf) == self.tf_group:
                print(f"[BarsLoader] buffer full, aggregating {len(buf)} bars into tf_bar")
                tf_bar = self.aggregate_bars(buf, group_size=len(buf))[0]
                print(f"[BarsLoader] emitting tf_bar: {tf_bar}")
                self.socketio.emit('bar', tf_bar)
                buf.clear()

            self.socketio.sleep(0.1)

        print(f"[BarsLoader] ◼ END stream_5m_bars for {pair}, final idx={self.current_5m_index[pair]}")
        self.streaming_5m[pair] = False