import csv
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser
from typing import Callable, Dict, List, Optional

from .combined_datasource import CombinedDataSource

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
        'EURUSD': 'csvs/EURUSD_2019.csv',
        'NQ':      'csvs/NQ_21-24.csv',
    }

    def __init__(
        self,
        pair: str,
        filename:       str   = None,
        time_fmt:       str   = None,
        tz:             str   = None,
        initial_start_time:     int   = None,  # UNIX epoch cutoff or datetime
        initial_end_time:       int   = None,  # UNIX epoch cutoff or datetime
        bars_per_second: float = 10.0
    ):
        # normalize datetime inputs to epoch ints
        if isinstance(initial_start_time, datetime):
            initial_start_time = int(initial_start_time.timestamp())
        if isinstance(initial_end_time, datetime):
            initial_end_time = int(initial_end_time.timestamp())

        # remember the user’s desired historical window
        self.initial_start_time = initial_start_time
        self.initial_end_time   = initial_end_time

        self.pair           = pair
        self.fmt            = time_fmt or self.PAIR_FORMATS.get(pair, self.DEFAULT_FMT)
        self.local_tz       = ZoneInfo(tz or self.PAIR_TZS.get(pair, 'UTC'))
        self.file           = filename or self.PAIR_FILES[pair]
        self.utc            = ZoneInfo("UTC")
        self.bars_per_second = bars_per_second
        self._emit_delay     = 1.0 / bars_per_second

        # 1) Load all raw 1m bars
        self._bars = self._load_historical_bars()

        # Replay state
        self.current_1m_index = 0
        self._from_time       = 0
        self.streaming        = False
        self._stop_event      = threading.Event()
        self._thread          = None

        # Aggregation state
        self.current_tf           = '1m'
        self.group_size           = 1
        self._1m_buffer           = []
        self._current_group_start = None

    def _load_historical_bars(self) -> List[Dict]:
        bars: List[Dict] = []
        ny_tz = ZoneInfo("America/New_York")
        with open(self.file, newline='') as f:
            sample  = f.read(2048); f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            reader  = csv.DictReader(f, dialect=dialect)

            for row in reader:
                ts = f"{row['Date']} {row['Time']}"
                try:
                    dt = datetime.strptime(ts, self.fmt)
                except ValueError:
                    dt = parser.parse(ts)
                dt_utc = dt.replace(tzinfo=self.utc)
                dt_ny  = dt_utc.astimezone(ny_tz)

                bars.append({
                    "time":   int(dt_ny.timestamp()),
                    "open":   float(row["Open"]),
                    "high":   float(row["High"]),
                    "low":    float(row["Low"]),
                    "close":  float(row["Close"]),
                    "volume": int(row.get("Volume", 0)),
                    "pair":   self.pair
                })
        return bars

    def load_historical_bars(
        self,
        timeframe: str = '1m',
        start_time: Optional[int] = None
    ) -> List[Dict]:
        # 1) take the full CSV in memory
        hist = self._bars

        # 2) apply the user’s initial_start/end window
        if self.initial_start_time is not None:
            hist = [b for b in hist if b['time'] >= self.initial_start_time]
        if self.initial_end_time is not None:
            hist = [b for b in hist if b['time'] <= self.initial_end_time]

        self._played_bars = list(hist)

        # 3) if they want raw 1 m, slice by start_time and return
        if timeframe == '1m':
            if start_time is not None:
                hist = [b for b in hist if b['time'] >= start_time]
            return list(hist)

        # 4) otherwise ignore start_time and aggregate the full slice
        return self._aggregate_whole_history_from_list(hist, timeframe)

    def subscribe(self, callback: Callable[[Dict], None], from_time: int = 0):
        print(f"[DEBUG] CSVDataSource.subscribe() replaying from {from_time}, total bars={len(self._bars)}")
        # self.set_timeframe(timeframe)

        for idx, bar in enumerate(self._bars):
            if getattr(self, '_stop_event', None) and self._stop_event.is_set():
                print("[DEBUG] CSVDataSource.subscribe() saw stop_event, exiting")
                break

            ts = bar['time']
            if ts <= from_time:
                continue

            # record that we’ve “played” this bar
            self._played_bars.append(bar)

            print(f"[DEBUG] CSV subscribe → emitting bar {idx} time={ts}")
            callback(bar)

        print("[DEBUG] CSVDataSource.subscribe() finished replay loop")

    def set_timeframe(self, tf: str):
        """Change target timeframe (e.g. '1m','5m','1h'). Resets replay state."""
        # stop existing thread if running
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join()

        # parse tf
        unit = tf[-1]
        num  = int(tf[:-1])
        if unit == 'm':
            self.group_size = max(1, num)
        elif unit == 'h':
            self.group_size = max(1, num * 60)
        else:
            raise ValueError(f"Unsupported timeframe '{tf}'")

        self.current_tf           = tf
        self.current_1m_index     = 0
        self._from_time           = 0
        self._1m_buffer           = []
        self._current_group_start = None
        self._stop_event.clear()

    def start(self, from_time: int = None):
        """Begin (or resume) replay from `from_time`."""
        if from_time is not None:
            self._from_time = from_time

        # align index on first start
        if self.current_1m_index == 0:
            idx = next(
                (i for i, b in enumerate(self._bars) if b["time"] > self._from_time),
                len(self._bars)
            )
            self.current_1m_index = idx

        self.streaming   = True
        self._stop_event.clear()
        self._thread     = threading.Thread(target=self._run_replay, daemon=True)
        self._thread.start()

    def pause(self):
        """Pause the replay thread, preserving buffer & index."""
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        self.streaming = False

    def seek(self, from_time: int):
        """Jump to a new from_time; resets grouping buffer."""
        self._from_time           = from_time
        idx = next(
            (i for i, b in enumerate(self._bars) if b["time"] >= from_time),
            len(self._bars)
        )
        self.current_1m_index     = idx
        self._1m_buffer           = []
        self._current_group_start = None

    def _run_replay(self):
        """Private: replay loop that processes each raw 1m bar."""
        total = len(self._bars)
        i     = self.current_1m_index

        while i < total and not self._stop_event.is_set():
            bar = self._bars[i]
            ts  = bar["time"]

            # skip if before from_time
            if ts > self._from_time:
                self.current_1m_index = i + 1
                self._process_bar(bar)

            i += 1
            time.sleep(self._emit_delay)

        self.streaming = False

    def _process_bar(self, bar: Dict):
        """Handle one raw 1m bar: either emit immediately or buffer+aggregate."""
        if self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1:
            # 1m: just emit
            self.callback(bar)
            return

        # higher TF: group into clock windows
        window_secs = self.group_size * 60
        ts          = bar["time"]
        start       = (ts // window_secs) * window_secs

        if self._current_group_start is None:
            self._current_group_start = start

        if start == self._current_group_start:
            self._1m_buffer.append(bar)
        else:
            # window closed → emit aggregated bar
            agg = self._aggregate_time_window(
                self._1m_buffer,
                self._current_group_start,
                window_secs
            )
            self.callback(agg)

            # reset buffer for next window
            self._1m_buffer           = [bar]
            self._current_group_start = start

    def _aggregate_whole_history_from_list(self, bars: List[Dict], tf: str) -> List[Dict]:
        unit = tf[-1]
        num  = int(tf[:-1])
        if unit == 'm':
            window_secs = num * 60
        elif unit == 'h':
            window_secs = num * 3600
        else:
            raise ValueError(f"Unsupported timeframe '{tf}'")

        from collections import defaultdict
        buckets: Dict[int, List[Dict]] = defaultdict(list)
        for bar in bars:
            win = (bar["time"] // window_secs) * window_secs
            buckets[win].append(bar)

        agg_bars = []
        for win in sorted(buckets.keys()):
            group = buckets[win]
            if group:
                agg_bars.append(
                    self._aggregate_time_window(group, win, window_secs)
                )

        return agg_bars
    
    def _aggregate_time_window(
        self,
        bars: List[Dict],
        window_start: int,
        window_secs: int
    ) -> Dict:
        """Combine a list of 1m bars into one TF bar."""
        open_  = bars[0]["open"]
        close_ = bars[-1]["close"]
        high   = max(b["high"] for b in bars)
        low    = min(b["low"]  for b in bars)
        volume = sum(b["volume"] for b in bars)

        return {
            "time":   window_start + window_secs,
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close_,
            "volume": volume,
            "pair":   bars[0]["pair"]
        }

    def _aggregate_whole_history(self, tf: str) -> List[Dict]:
        """
        One-off aggregation of the entire loaded history into TF bars,
        used by load_historical_bars(tf) when tf != '1m'.
        """
        unit = tf[-1]
        num  = int(tf[:-1])
        if unit == 'm':
            window_secs = num * 60
        elif unit == 'h':
            window_secs = num * 3600
        else:
            raise ValueError(f"Unsupported timeframe '{tf}'")

        from collections import defaultdict
        buckets: Dict[int, List[Dict]] = defaultdict(list)

        for bar in self._bars:
            win = (bar["time"] // window_secs) * window_secs
            buckets[win].append(bar)

        agg_bars = []
        for win in sorted(buckets.keys()):
            group = buckets[win]
            if group:
                agg_bars.append(
                    self._aggregate_time_window(group, win, window_secs)
                )

        return agg_bars
