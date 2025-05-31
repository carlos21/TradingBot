# src/bars_loader.py

from flask import abort
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser
import threading
import time  # for small sleeps if needed

from src.data_sources.combined_datasource import CombinedDataSource
from flask_socketio import SocketIO


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
    INITIAL_END         = parser.parse("2024-08-30T23:59:59Z")

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
    - Builds any TF bar by grouping exactly group_size consecutive 1m bars.
    - Emits 1m bars immediately when TF='1m'; otherwise groups and emits larger bars.
    - Checks each incoming bar for SL/TP (via bar_callback).
    - Uses a single CombinedDataSource to supply historical bars and live ticks.
    - Ensures only one replay thread is running at a time; pressing Pause stops it.
    """

    def __init__(
        self,
        config: BarsConfig,
        data_source: CombinedDataSource,
        socketio: SocketIO,
        strategy=None,
        bar_callback: callable = None
    ):
        self.config       = config
        self.data_source  = data_source
        self.socketio     = socketio
        self.strategy     = strategy
        self.bar_callback = bar_callback
        self.stream_lock  = threading.Lock()

        # Load full 1m history for REST slicing and pointer tracking
        self.pair          = data_source.pair
        self.raw_1m        = data_source.load_historical_bars()

        # Streaming state
        self.current_1m_index = 0   # index for REST endpoint
        self.streaming_1m     = False
        self._from_time       = 0   # timestamp to skip up to on replay

        # A single rolling buffer for grouping N consecutive 1m bars
        self._1m_buffer = []

        # Current timeframe and grouping size (minutes)
        self.current_tf = '5m'
        self.group_size = 5  # default: 5m => group_size=5

        # Thread‐management flags
        self._replay_thread = None
        self._stop_replay   = threading.Event()

    def set_timeframe(self, tf: str):
        """
        Set user TF like '1m','5m','15m','1h','4h', etc. Compute how many
        consecutive 1m bars are needed (group_size) and clear any partial buffer.
        """
        self.current_tf = tf
        unit = tf[-1]
        num  = int(tf[:-1])
        # Number of minutes in this timeframe: e.g., '5m'->5, '1h'->60, '4h'->240
        minutes = num * (60 if unit == 'h' else 1)
        self.group_size = max(1, minutes)

        # Clear any half‐built 1m buffer
        with self.stream_lock:
            self._1m_buffer.clear()

    def start(self, from_time: int = None):
        """
        Begin streaming. 'from_time' is the UNIX timestamp to skip all bars <= that.
        If a previous replay thread is still running, signal it to stop and wait
        until it exits, then start a brand-new replay thread.
        """
        # 1) Set the cutoff timestamp
        self._from_time = from_time or 0

        # 2) Clear the stop signal so the new thread can run
        self._stop_replay.clear()

        # 3) If an old replay thread is still alive, wait for it to finish
        if self._replay_thread and self._replay_thread.is_alive():
            # Signal the old thread to stop
            self._stop_replay.set()
            # Wait for it to see the flag and exit
            self._replay_thread.join()

        # 4) Now un‐pause streaming
        self.streaming_1m = True

        # 5) Launch a brand-new replay thread that calls `_handle_message`
        self._replay_thread = threading.Thread(target=self._run_replay, daemon=True)
        self._replay_thread.start()

    def pause(self):
        """Temporarily stop processing incoming messages (and wind down the replay thread)."""
        # Signal the replay thread to stop
        self._stop_replay.set()
        self.streaming_1m = False

        # Wait for the thread to cleanly exit
        if self._replay_thread and self._replay_thread.is_alive():
            self._replay_thread.join()

        self._replay_thread = None

    def _run_replay(self):
        """
        Internal loop that runs in its own thread.  It iterates over all historical
        bars in order, skipping any with time <= self._from_time, and emits exactly
        10 bars per second (sleeping 0.1s between each).  If at any point pause() is
        called, self._stop_replay will be set, and this loop will break.
        """
        prev_ts = None
        for bar in self.raw_1m:
            # If Pause has been called, exit immediately
            if self._stop_replay.is_set():
                return

            ts = bar["time"]
            if ts <= self._from_time:
                continue

            # 1) forward this bar to our handler (for grouping/SL‐TP)
            self._handle_message(bar)

            # 2) Sleep exactly 0.1s (i.e. 10 bars/sec) *unless* Pause came in
            for _ in range(10):
                if self._stop_replay.is_set():
                    return
                time.sleep(0.01)  # 10 × 0.01s = 0.1s

        # Once we've exhausted raw_1m, the thread simply exits

    def _handle_message(self, msg: dict):
        """
        Called by our replay thread (and in the future by live ticks).  Each msg
        is either a 1m bar (has 'open','high',etc.) or a tick (has 'price').
        We:
          - Drop it if streaming is paused.
          - Drop it if msg['time'] <= self._from_time.
          - Otherwise, pass it to _process_bar or _process_tick.
        """
        if not self.streaming_1m:
            return

        ts = msg.get('time', 0)
        if ts <= self._from_time:
            return

        if 'open' in msg and 'high' in msg:
            self._process_bar(msg)
        elif 'price' in msg:
            self._process_tick(msg)

    def _process_bar(self, bar1: dict):
        """
        Handle a single 1m bar:
          1) SL/TP callback on raw 1m.
          2) If current_tf == '1m', emit immediately.
          3) Otherwise, buffer bar1; once buffer length == group_size,
             aggregate+emit one TF bar, then clear buffer.
        """
        # 1) SL/TP check on raw 1m
        if self.bar_callback:
            self.bar_callback(bar1)

        # 2) If TF == '1m', emit immediately
        if self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1:
            self.socketio.emit('bar', bar1)
            return

        # 3) Otherwise, group consecutive 1m bars into one TF bar
        with self.stream_lock:
            self._1m_buffer.append(bar1)

            if len(self._1m_buffer) == self.group_size:
                # Aggregate exactly group_size 1m bars into one TF bar
                tf_bar = self._aggregate_bars(self._1m_buffer, self.group_size)[0]
                self._1m_buffer.clear()

                # Feed the strategy with the new TF bar
                if self.strategy:
                    try:
                        self.strategy.on_new_bar(tf_bar)
                    except Exception:
                        import traceback; traceback.print_exc()

                # Emit the aggregated TF bar
                self.socketio.emit('bar', tf_bar)

    def _process_tick(self, tick: dict):
        """
        Handle a live tick (no grouping), perform SL/TP callback if desired,
        and emit 'tick' event to front end.
        """
        if self.bar_callback:
            self.bar_callback(tick)
        self.socketio.emit('tick', tick)

    def _aggregate_bars(self, bars: list, group_size: int = 5) -> list:
        """
        Generic aggregator: takes a list of consecutive 1m bar‐dicts and
        merges them into one bar of length = group_size minutes.

        Each bar in `bars` must have keys: time, open, high, low, close, volume, pair.
        Returns a list of aggregated bars (could be multiple if len(bars) > group_size).
        """
        if len(bars) < 2:
            base_interval = 60
        else:
            base_interval = bars[1]['time'] - bars[0]['time']
        window = group_size * base_interval

        agg = []
        for i in range(0, len(bars), group_size):
            chunk = bars[i : i + group_size]
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

    def prepare_agg_bars(self, tf: str, start_time: int = None) -> list:
        """
        The REST GET /api/bars endpoint:
         - If tf == '1m', return a slice of raw_1m between INITIAL_START..start_time.
         - Otherwise, group raw_1m into tf‐bars on the fly and slice by time window.
        """
        # 1m case: just return raw 1m slice
        if tf.endswith('m') and int(tf[:-1]) == 1:
            if start_time is None:
                start_ts = int(self.config.INITIAL_START.timestamp())
                end_ts   = int(self.config.INITIAL_END.timestamp())
            else:
                start_ts, end_ts = 0, start_time

            idx = next((i for i, b in enumerate(self.raw_1m) if b['time'] > end_ts),
                       len(self.raw_1m))
            self.current_1m_index = idx
            return [b for b in self.raw_1m if start_ts <= b['time'] <= end_ts]

        # Non-1m: compute group_size_minutes and group raw_1m directly
        num, unit = int(tf[:-1]), tf[-1]
        mins = num * (60 if unit == 'h' else 1)

        # Slice raw_1m by time window first
        if start_time is None:
            start_ts = int(self.config.INITIAL_START.timestamp())
            end_ts   = int(self.config.INITIAL_END.timestamp())
        else:
            start_ts, end_ts = 0, start_time

        sliced = [b for b in self.raw_1m if start_ts <= b['time'] <= end_ts]

        # Advance pointer on raw_1m for streaming sync
        idx = next((i for i, b in enumerate(self.raw_1m) if b['time'] > end_ts),
                   len(self.raw_1m))
        self.current_1m_index = idx

        # Group sliced 1m bars into TF bars of length=mins
        return self._aggregate_bars(sliced, mins)