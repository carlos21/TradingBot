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
    def __init__(self, config: BarsConfig, socketio, strategy=None, bar_callback: callable = None):
        self.config      = config
        self.socketio    = socketio
        self.strategy    = strategy
        self.bar_callback = bar_callback
        self.stream_lock = threading.Lock()

        # raw 1m data
        self.all_1m_data      = { pair: self._load_csv_data(path) for pair, path in self.config.CSV_FILES.items() }
        # cache for REST (5m)
        self._agg_5m_cache    = { pair: self._aggregate_bars(raw, 5) for pair, raw in self.all_1m_data.items() }

        # streaming pointers
        self.current_1m_index = { pair: 0 for pair in self.all_1m_data }
        self.streaming_1m     = { pair: False for pair in self.all_1m_data }

        # aggregation buffers
        self._5m_buffer = { pair: [] for pair in self.all_1m_data }
        self.tf_buffer  = { pair: [] for pair in self.all_1m_data }

        # timeframe grouping (# of 5m bars per TF)
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
        for pair in self.all_1m_data:
            self._5m_buffer[pair].clear()
            self.tf_buffer[pair].clear()

    def _load_csv_data(self, filename: str, time_format: str = None):
        """Read raw 1m CSV and return list of bars with UTC timestamps."""
        pair = os.path.splitext(os.path.basename(filename))[0].split('_')[0]
        fmt      = time_format or self.config.TIME_FORMATS.get(pair, self.config.DEFAULT_TIME_FMT)
        local_tz = ZoneInfo(self.config.PAIR_TIMEZONES.get(pair, self.config.DEFAULT_TIMEZONE))
        utc_tz   = ZoneInfo('UTC')

        data = []
        with open(filename, 'r', newline='') as f:
            sample  = f.read(2048); f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=";,")
            reader  = csv.DictReader(f, dialect=dialect)
            for row in reader:
                ts_str = f"{row.get('Date','')} {row.get('Time','')}".strip()
                try:
                    dt = datetime.strptime(ts_str, fmt)
                except Exception:
                    dt = parser.parse(ts_str)
                dt = dt.replace(tzinfo=local_tz).astimezone(utc_tz)
                data.append({
                    'time':   int(dt.timestamp()),
                    'open':   float(row['Open']),
                    'high':   float(row['High']),
                    'low':    float(row['Low']),
                    'close':  float(row['Close']),
                    'volume': int(row.get('Volume', 0)),
                    'pair':   pair
                })
        data.sort(key=lambda b: b['time'])
        return data

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

    def prepare_agg_bars(self, pair: str, tf: str, start_time: int=None):
        """
        REST GET /api/bars:
        If 1m selected, return raw 1m bars; otherwise re-aggregate 5m cache into TF.
        """
        raw_1m = self.all_1m_data.get(pair)
        if raw_1m is None:
            abort(400, f"Unknown pair '{pair}'")

        # 1m timeframe: return raw
        if tf.endswith('m') and int(tf[:-1]) == 1:
            if start_time is None:
                start_ts = int(self.config.INITIAL_START.timestamp())
                end_ts   = int(self.config.INITIAL_END.timestamp())
            else:
                start_ts, end_ts = 0, start_time

            # advance pointer past window
            idx = next((i for i,b in enumerate(raw_1m) if b['time'] > end_ts), len(raw_1m))
            self.current_1m_index[pair] = idx

            # slice raw data
            return [b for b in raw_1m if start_ts <= b['time'] <= end_ts]

        # non-1m: use cached 5m and re-aggregate
        base_5m = self._agg_5m_cache[pair]
        num  = int(tf[:-1])
        unit = tf[-1]
        mins = num * (60 if unit == 'h' else 1)
        grp  = mins // 5 or 1
        agg_tf = self._aggregate_bars(base_5m, group_size=grp)

        if start_time is None:
            start_ts = int(self.config.INITIAL_START.timestamp())
            end_ts   = int(self.config.INITIAL_END.timestamp())
        else:
            start_ts, end_ts = 0, start_time

        # advance pointer on raw 1m based on end_ts
        idx = next((i for i,b in enumerate(raw_1m) if b['time'] > end_ts), len(raw_1m))
        self.current_1m_index[pair] = idx

        return [b for b in agg_tf if start_ts <= b['time'] <= end_ts]

    def stream_1m_bars(self, pair: str):
        """
        Single 1m loop: builds 5m bars for strategy, re-aggregates into TF bars,
        calls SL/TP callback on raw 1m, and emits TF bars.
        """
        raw   = self.all_1m_data[pair]
        idx   = self.current_1m_index[pair]
        self.streaming_1m[pair] = True

        while self.streaming_1m[pair] and idx < len(raw):
            with self.stream_lock:
                bar1 = raw[idx]
                idx += 1
                self.current_1m_index[pair] = idx

            # SL/TP check
            if self.bar_callback:
                self.bar_callback(bar1)

            # accumulate into 5m bars
            buf5 = self._5m_buffer[pair]
            buf5.append(bar1)
            if len(buf5) == 5:
                agg5 = self._aggregate_bars(buf5, 5)[0]
                # feed strategy
                if self.strategy:
                    try: self.strategy.on_new_bar(agg5)
                    except Exception: import traceback; traceback.print_exc()
                buf5.clear()

                # accumulate into TF bars
                buftf = self.tf_buffer[pair]
                buftf.append(agg5)
                if len(buftf) == self.tf_group:
                    tf_bar = self._aggregate_bars(buftf, len(buftf))[0]
                    self.socketio.emit('bar', tf_bar)
                    buftf.clear()

                    # throttle replay speed
                    self.socketio.sleep(0.1)

        self.streaming_1m[pair] = False