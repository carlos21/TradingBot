import csv
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser
from typing import Callable, Dict, List

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
        filename: str = None,
        time_fmt: str = None,
        tz: str = None,
        bars_per_second: float = 10.0
    ):
        # identity & CSV parsing config
        self.pair      = pair
        self.fmt       = time_fmt or self.PAIR_FORMATS.get(pair, self.DEFAULT_FMT)
        self.local_tz  = ZoneInfo(tz or self.PAIR_TZS.get(pair, 'UTC'))
        self.file      = filename or self.PAIR_FILES[pair]
        self.utc       = ZoneInfo("UTC")

        # load all raw 1m bars into memory, timestamped in UTC
        self._bars     = self._load_historical_bars()

        # replay / streaming state
        self.bars_per_second      = bars_per_second
        self._emit_delay          = 1.0 / bars_per_second
        self.current_1m_index     = 0
        self._from_time           = 0
        self.streaming            = False
        self._stop_event          = threading.Event()
        self._thread              = None

        # grouping state for higher‐TF bars
        self.current_tf           = '1m'
        self.group_size           = 1          # in minutes
        self._1m_buffer           = []
        self._current_group_start = None       # epoch‐sec start of current window

    def _load_historical_bars(self) -> List[Dict]:
        bars: List[Dict] = []
        ny_tz = ZoneInfo("America/New_York")

        with open(self.file, newline='') as f:
            sample  = f.read(2048)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=";,")
            reader  = csv.DictReader(f, dialect=dialect)

            for row in reader:
                ts = f"{row['Date']} {row['Time']}"
                try:
                    dt = datetime.strptime(ts, self.fmt)
                except ValueError:
                    dt = parser.parse(ts)

                # label as UTC, convert to NY, then take epoch in that zone
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

    def load_historical_bars(self, timeframe: str = '1m') -> List[Dict]:
        """
        Return historical bars:
          - If '1m', it's just the raw _bars list.
          - Else, return clock‐aligned aggregated bars.
        """
        if timeframe.endswith('m') and int(timeframe[:-1]) == 1:
            return list(self._bars)

        # For higher TF, aggregate once and return
        return self._aggregate_whole_history(timeframe)

    def subscribe(
        self,
        callback: Callable[[Dict], None],
        from_time: int = 0,
        timeframe: str = '1m'
    ) -> None:
        """
        Begin replay of historical bars > from_time, at bars_per_second:
          - Calls callback(bar) for each replayed (or aggregated) bar.
          - Supports pause() / seek() on the fly.
        """
        # wire up
        self.callback = callback
        self.set_timeframe(timeframe)
        self.start(from_time)

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
