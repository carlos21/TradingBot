import csv
import threading
import time
import logging

from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser
from typing import Callable, Dict, List, Optional

from .combined_datasource import CombinedDataSource

# Configure module-level logger
t_logger = logging.getLogger(__name__)
t_logger.setLevel(logging.DEBUG)
if not t_logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    fmt = logging.Formatter('[%(asctime)s] %(levelname)s %(message)s')
    handler.setFormatter(fmt)
    t_logger.addHandler(handler)

class CSVDataSource(CombinedDataSource):
    """
    A CombinedDataSource that:
      - Loads 1m bars from a CSV on init
      - Can replay those bars at configurable speed
      - Supports pause(), seek(), and changing timeframe on the fly
      - Aggregates raw 1m bars into higher‐TF bars
    """

    DEFAULT_FMT  = '%d/%m/%Y %H:%M:%S'
    PAIR_FORMATS = {
        'EURUSD': '%Y.%m.%d %H:%M',
        'NQ':      '%d/%m/%Y %H:%M:%S',
    }
    PAIR_TZS     = {
        'EURUSD': 'Europe/London',
        'NQ':      'America/Chicago',
    }
    PAIR_FILES   = {
        'EURUSD': 'csvs/EURUSD_2024.csv',
        'NQ':      'csvs/NQ_21-24.csv',
    }

    def __init__(
        self,
        pair: str,
        filename:       str   = None,
        time_fmt:       str   = None,
        tz:             str   = None,
        initial_start_time:     int   = None,
        initial_end_time:       int   = None,
        bars_per_second: float = 10.0,
        fileobj=None
    ):
        # normalize datetime inputs to epoch ints
        if isinstance(initial_start_time, datetime):
            initial_start_time = int(initial_start_time.timestamp())
        if isinstance(initial_end_time, datetime):
            initial_end_time = int(initial_end_time.timestamp())

        self.initial_start_time = initial_start_time
        self.initial_end_time   = initial_end_time
        self.pair               = pair
        self.fmt                = time_fmt or self.PAIR_FORMATS.get(pair, self.DEFAULT_FMT)
        self.local_tz           = ZoneInfo(tz or self.PAIR_TZS.get(pair, 'UTC'))
        self.file               = filename or self.PAIR_FILES[pair]
        self._fileobj           = fileobj
        self.utc                = ZoneInfo("UTC")
        self.bars_per_second    = bars_per_second
        self._emit_delay        = 1.0 / bars_per_second

        # 1) Load all raw 1m bars
        self._bars = self._load_historical_bars()
        t_logger.debug(f"Loaded {len(self._bars)} bars from CSV: {self.file}")

        # seed the "played" buffer with initial window
        self._played_bars = [
            b for b in self._bars
            if (self.initial_start_time is None or b['time'] >= self.initial_start_time)
            and (self.initial_end_time   is None or b['time'] <= self.initial_end_time)
        ]
        print(f"[CSV_DS] loaded {len(self._bars)} raw, seeded _played_bars with {len(self._played_bars)}")

        # Replay state
        self.current_1m_index     = 0
        self._from_time           = 0
        self.streaming            = False
        self._stop_event          = threading.Event()
        self._thread              = None

        # Aggregation state
        self.current_tf           = '1m'
        self.group_size           = 1
        self._1m_buffer           = []
        self._current_group_start = None

    def reset(self, start_time: int = None, end_time: int = None):
        """
        Resets the datasource to its initial state.
        Re-seeds _played_bars with the full allowed range.
        
        :param start_time: Optional epoch timestamp to start the history buffer.
        :param end_time:   Optional epoch timestamp to end the history buffer.
        """
        t_logger.debug(f"[CSV_DS] Resetting state... start={start_time}, end={end_time}")
        
        # Use provided bounds or fall back to initial config
        s = start_time if start_time is not None else self.initial_start_time
        e = end_time   if end_time   is not None else self.initial_end_time

        # Re-seed _played_bars from master _bars based on bounds
        self._played_bars = [
            b for b in self._bars
            if (s is None or b['time'] >= s)
            and (e is None or b['time'] <= e)
        ]
        
        self.current_1m_index = 0
        self._1m_buffer = []
        self._current_group_start = None
        
        # Stop any running replay thread
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
             self._thread.join(timeout=1.0)
        self._stop_event.clear()
        
        t_logger.info(f"[CSV_DS] Reset complete. _played_bars re-seeded with {len(self._played_bars)} bars.")

    def _load_historical_bars(self) -> List[Dict]:
        bars: List[Dict] = []
        local_tz = self.local_tz             # e.g., America/Chicago for NQ
        with (self._fileobj or open(self.file, newline='')) as f:
            sample  = f.read(2048); f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            reader  = csv.DictReader(f, dialect=dialect)

            for row in reader:
                ts = f"{row['Date']} {row['Time']}"
                try:
                    dt = datetime.strptime(ts, self.fmt)   # naive
                except ValueError:
                    dt = parser.parse(ts)                  # still naive

                # Treat the CSV timestamp as local (pair) time, then convert to UTC
                dt_local = dt.replace(tzinfo=local_tz)
                dt_utc   = dt_local.astimezone(self.utc)

                bars.append({
                    'time':   int(dt_utc.timestamp()),
                    'open':   float(row['Open']),
                    'high':   float(row['High']),
                    'low':    float(row['Low']),
                    'close':  float(row['Close']),
                    'volume': int(row.get('Volume', 0)),
                    'pair':   self.pair
                })
        return bars

    def load_historical_bars(self, timeframe='1m', start_time=None):
        print(f"[CSV_DS] load_historical_bars → tf={timeframe!r}, start_time={start_time!r}, "
            f"_played_bars_len={len(self._played_bars)}")
        
        # Filter source bars based on start_time if provided
        source = self._played_bars
        if start_time is not None:
            source = [b for b in source if b['time'] >= start_time]

        if timeframe == '1m':
            print(f"[CSV_DS] → returning {len(source)} 1m bars")
            return list(source)

        # ─── Higher TFs: aggregate the filtered buffer ───
        t_logger.debug(f"Aggregating {len(source)} bars into {timeframe}")
        return self._aggregate_whole_history_from_list(source, timeframe)

    def subscribe(self, callback, from_time=0):
        print(f"[CSV_DS] subscribe() start → from_time={from_time}, total_bars={len(self._bars)}")
        
        # 1. Truncate _played_bars to remove any history AFTER from_time
        #    This prevents duplicates when seeking back or resuming.
        self._played_bars = [b for b in self._played_bars if b['time'] < from_time]
        print(f"[CSV_DS] Truncated _played_bars to {len(self._played_bars)} items (before {from_time})")

        # 2. Find start index in the master list
        start_idx = next((i for i,b in enumerate(self._bars) if b['time'] >= from_time), None)

        if start_idx is not None:
            print(f"[CSV_DS] first to play idx={start_idx}, ts={self._bars[start_idx]['time']}")
        else:
            print("[CSV_DS] no bars to play after from_time")
            # If we are at the end, just return
            try:
                callback({'_end': True})
            except Exception:
                pass
            return

        # 3. Stream bars
        for idx in range(start_idx, len(self._bars)):
            if self._stop_event.is_set():
                print("[CSV_DS] saw stop_event, breaking")
                break
            
            bar = self._bars[idx]
            
            # Append to history
            self._played_bars.append(bar)
            
            # Emit
            callback(bar)
        
        # End of stream
        if not self._stop_event.is_set():
            try:
                callback({'_end': True})
            except Exception:
                pass

    def set_timeframe(self, tf: str):
        t_logger.debug(f"set_timeframe called → tf={tf}")
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join()

        unit = tf[-1]
        num  = int(tf[:-1])
        if unit == 'm':        self.group_size = max(1, num)
        elif unit == 'h':      self.group_size = max(1, num * 60)
        else:                  raise ValueError(f"Unsupported timeframe '{tf}'")

        self.current_tf           = tf
        self.current_1m_index     = 0
        self._from_time           = 0
        self._1m_buffer           = []
        self._current_group_start = None
        self._stop_event.clear()
        t_logger.debug(f"Frame set: group_size={self.group_size}, current_tf={self.current_tf}")

    def start(self, from_time: int = None):
        if from_time is not None:
            self._from_time = from_time
        if self.current_1m_index == 0:
            idx = next(
                (i for i, b in enumerate(self._bars) if b['time'] >= self._from_time),
                len(self._bars)
            )
            self.current_1m_index = idx
        self.streaming = True
        self._stop_event.clear()
        t_logger.debug(f"start() → from_time={self._from_time}, starting idx={self.current_1m_index}")
        self._thread = threading.Thread(target=self._run_replay, daemon=True)
        self._thread.start()

    def pause(self):
        t_logger.debug("pause() called")
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        self.streaming = False

    def seek(self, from_time: int):
        self._from_time = from_time
        idx = next(
            (i for i, b in enumerate(self._bars) if b['time'] >= from_time),
            len(self._bars)
        )
        self.current_1m_index     = idx
        self._1m_buffer           = []
        self._current_group_start = None
        t_logger.debug(f"seek() → new from_time={from_time}, new index={idx}")

    def _run_replay(self):
        total = len(self._bars)
        i     = self.current_1m_index
        t_logger.debug(f"_run_replay() enter loop → i={i}, total={total}")

        while i < total and not self._stop_event.is_set():
            bar = self._bars[i]
            ts  = bar['time']
            if ts >= self._from_time:
                self.current_1m_index = i + 1
                self._process_bar(bar)
            i += 1
            time.sleep(self._emit_delay)

        self.streaming = False
        t_logger.debug("_run_replay() exiting loop, streaming=False")

    def _process_bar(self, bar: Dict):
        if self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1:
            t_logger.debug(f"_process_bar → emitting raw 1m bar time={bar['time']}")
            self.callback(bar)
            return

        window_secs = self.group_size * 60
        ts          = bar['time']
        start       = (ts // window_secs) * window_secs
        if self._current_group_start is None:
            self._current_group_start = start
        if start == self._current_group_start:
            self._1m_buffer.append(bar)
            t_logger.debug(f"_process_bar → buffering bar time={ts} to window {start}")
        else:
            agg = self._aggregate_time_window(
                self._1m_buffer, self._current_group_start, window_secs
            )
            t_logger.debug(f"_process_bar → emitting aggregated bar for window {self._current_group_start}: {agg}")
            self.callback(agg)
            self._1m_buffer           = [bar]
            self._current_group_start = start

    def _aggregate_whole_history_from_list(self, bars: List[Dict], tf: str) -> List[Dict]:
        unit = tf[-1]
        num  = int(tf[:-1])
        if unit == 'm':        window_secs = num * 60
        elif unit == 'h':      window_secs = num * 3600
        else:                  raise ValueError(f"Unsupported timeframe '{tf}'")

        from collections import defaultdict
        buckets: Dict[int, List[Dict]] = defaultdict(list)
        for bar in bars:
            win = (bar['time'] // window_secs) * window_secs
            buckets[win].append(bar)

        agg_bars = []
        for win in sorted(buckets):
            group = buckets[win]
            if group:
                agg_bars.append(
                    self._aggregate_time_window(group, win, window_secs)
                )
        return agg_bars

    def _aggregate_time_window(
        self, bars: List[Dict], window_start: int, window_secs: int
    ) -> Dict:
        open_  = bars[0]['open']
        close_ = bars[-1]['close']
        high   = max(b['high'] for b in bars)
        low    = min(b['low']  for b in bars)
        volume = sum(b['volume'] for b in bars)
        return {
            'time':   window_start,
            'open':   open_,
            'high':   high,
            'low':    low,
            'close':  close_,
            'volume': volume,
            'pair':   bars[0]['pair']
        }