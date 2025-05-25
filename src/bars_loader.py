from flask import abort
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser
import os
import csv
import threading

from src.data_sources.bars_datasource import BarsDataSource


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
        'EURUSD': 'csvs/EURUSD_2019.csv',
        'NQ':      'csvs/NQ_2024.csv',
    }
    INITIAL_START       = parser.parse("2024-08-01T00:00:00Z")
    INITIAL_END         = parser.parse("2024-08-30T23:59:59Z")  # extend to end of January

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
    Drives everything off a single 1m loop:
    - builds 5m bars for the strategy
    - re-aggregates into user TF for emitting 'bar'
    - checks every 1m bar for SL/TP and closes trades immediately
    """
    def __init__(
        self, 
        config: BarsConfig,
        data_source: BarsDataSource,
        socketio, 
        strategy=None, 
        bar_callback: callable = None
    ):
        self.config = config
        self.data_source = data_source
        self.socketio = socketio
        self.strategy = strategy
        self.bar_callback = bar_callback
        self.stream_lock = threading.Lock()

        # load raw 1m bars from your single data source
        # data_source.load_1m_bars() must return List[bar_dict]
        self.pair         = data_source.pair
        self.raw_1m       = data_source.load_1m_bars()

        # pre-aggregate the whole history into 5m
        self._agg_5m_cache = self._aggregate_bars(self.raw_1m, 5)

        # streaming state
        self.current_1m_index = 0
        self.streaming_1m     = False

        # buffers for building aggregates
        self._5m_buffer = []
        self.tf_buffer  = []

        # current TF and grouping
        self.current_tf = '5m'
        self.tf_group   = 1


    def set_timeframe(self, tf: str):
        """Set user TF like '1m','5m','15m','1h' and clear buffers."""
        self.current_tf = tf
        unit = tf[-1]
        num  = int(tf[:-1])
        minutes = num * (60 if unit == 'h' else 1)
        # group of 5m bars per TF = minutes/5
        grp = minutes // 5
        self.tf_group = grp if grp > 0 else 1

        # clear *both* the 5m‐ and TF‐aggregation buffers
        self._5m_buffer.clear()
        self.tf_buffer.clear()

    def _aggregate_bars(self, bars: list, group_size: int = 5):
        """Generic aggregator: used for 5m base and for TF grouping."""
        if len(bars) < 2:
            base_interval = 60
        else:
            base_interval = bars[1]['time'] - bars[0]['time']
        window = group_size * base_interval
        agg = []
        for i in range(0, len(bars), group_size):
            chunk = bars[i:i+group_size]
            if len(chunk) < group_size:
                break
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
                'pair':   chunk[0]['pair']
            })
        return agg

    def prepare_agg_bars(self, tf: str, start_time: int = None):
        """
        The REST /api/bars endpoint:
        - if 1m, return raw slice
        - else, re-aggregate the cached 5m into tf bars
        """
        if tf.endswith('m') and int(tf[:-1]) == 1:
            # raw 1m
            if start_time is None:
                start_ts = int(self.config.INITIAL_START.timestamp())
                end_ts   = int(self.config.INITIAL_END.timestamp())
            else:
                start_ts, end_ts = 0, start_time

            # advance pointer
            idx = next((i for i,b in enumerate(self.raw_1m) if b['time'] > end_ts),
                       len(self.raw_1m))
            self.current_1m_index = idx

            return [b for b in self.raw_1m if start_ts <= b['time'] <= end_ts]

        # non-1m → aggregate 5m cache
        num, unit = int(tf[:-1]), tf[-1]
        mins = num * (60 if unit == 'h' else 1)
        grp  = max(1, mins // 5)
        agg_tf = self._aggregate_bars(self._agg_5m_cache, grp)

        if start_time is None:
            start_ts = int(self.config.INITIAL_START.timestamp())
            end_ts   = int(self.config.INITIAL_END.timestamp())
        else:
            start_ts, end_ts = 0, start_time

        # also bump the raw-1m pointer so streaming stays in sync
        idx = next((i for i,b in enumerate(self.raw_1m) if b['time'] > end_ts),
                   len(self.raw_1m))
        self.current_1m_index = idx

        return [b for b in agg_tf if start_ts <= b['time'] <= end_ts]


    def stream_1m_bars(self):
        """Identical to before but only one stream loop."""
        self.streaming_1m = True
        idx = self.current_1m_index

        while self.streaming_1m and idx < len(self.raw_1m):
            with self.stream_lock:
                bar1 = self.raw_1m[idx]
                idx += 1
                self.current_1m_index = idx

            if self.bar_callback:
                self.bar_callback(bar1)

            self._5m_buffer.append(bar1)
            if len(self._5m_buffer) == 5:
                agg5 = self._aggregate_bars(self._5m_buffer, 5)[0]
                if self.strategy:
                    try: self.strategy.on_new_bar(agg5)
                    except: import traceback; traceback.print_exc()
                self._5m_buffer.clear()

                self.tf_buffer.append(agg5)
                if len(self.tf_buffer) == self.tf_group:
                    tf_bar = self._aggregate_bars(self.tf_buffer, len(self.tf_buffer))[0]
                    self.socketio.emit('bar', tf_bar)
                    self.tf_buffer.clear()
                    self.socketio.sleep(0.1)

        self.streaming_1m = False