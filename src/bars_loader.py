from typing import Callable, List
from dataclasses import dataclass
from flask_socketio import SocketIO
import threading
import time

from src.data_sources.combined_datasource import CombinedDataSource

@dataclass
class LoaderConfig:
    # initial_start and initial_end are applied in the data source, not here
    initial_start: int  # UNIX timestamp
    initial_end:   int  # UNIX timestamp

class BarsLoader:
    """
    Drives everything off a CombinedDataSource.subscribe push model:
    - Receives raw 1m bars and live ticks via the subscribe callback
    - Builds TF bars by grouping exactly `group_size` consecutive 1m bars aligned to real clock windows
    - Emits 1m bars immediately when TF='1m'; otherwise groups and emits larger bars
    - Checks each incoming bar/tick for SL/TP via `bar_callback`
    - Supports pause(), seek(), and changing timeframe on the fly
    """

    def __init__(
        self,
        data_source: CombinedDataSource,
        socketio: SocketIO,
        bar_callback: Callable[[dict], None] = None,
        bars_per_second: float = 10.0
    ):
        # config
        self.data_source     = data_source
        self.socketio        = socketio
        self.bar_callback    = bar_callback
        self.bars_per_second = bars_per_second
        self._emit_delay     = 1.0 / bars_per_second

        # replay state
        self._from_time    = 0
        self.streaming     = False
        self._stop_event   = threading.Event()
        self._thread       = None

        # grouping state
        self.current_tf           = '1m'
        self.group_size           = 1        # minutes for aggregation
        self._1m_buffer           = []
        self._current_group_start = None     # epoch-sec start of current window

    def set_timeframe(self, tf: str):
        """Change aggregation timeframe (e.g. '1m','5m','1h')."""
        # stop running loop
        if self.streaming:
            self._stop_event.set()
        unit = tf[-1]
        num  = int(tf[:-1])
        if unit == 'm':
            self.group_size = max(1, num)
        elif unit == 'h':
            self.group_size = max(1, num * 60)
        else:
            raise ValueError(f"Unsupported timeframe '{tf}'")
        self.current_tf           = tf
        self._1m_buffer           = []
        self._current_group_start = None
        self._stop_event.clear()

    def start(self, from_time: int = None):
        """
        Begin or resume streaming.   
        `from_time` is passed directly to the data-source.subscribe.
        """
        # optionally update from_time for next subscribe call
        if from_time is not None:
            self._from_time = from_time

        self.streaming     = True
        self._stop_event.clear()

        if hasattr(self.data_source, "_stop_event"):
            self.data_source._stop_event.clear()

        # run the subscribe loop in SocketIO’s context so emits work
        self.socketio.start_background_task(
            self.data_source.subscribe,
            self._handle_message,
            self._from_time
        )

    def pause(self):
        """
        Pause streaming.  
        - Signals our own loop to stop  
        - If the data source supports pause(), stop it too
        """
        # 1) stop our grouping & dispatch loop
        self._stop_event.set()

        # 2) if the data source has a pause() method, call it
        if hasattr(self.data_source, 'pause'):
            try:
                self.data_source.pause()
            except Exception:
                # swallow any errors so pause() is idempotent
                pass

        # 3) reflect paused state
        self.streaming = False

    def seek(self, from_time: int):
        """Prepare to replay from a new timestamp on next start()."""
        self._from_time           = from_time
        self._1m_buffer           = []
        self._current_group_start = None

    def _handle_message(self, msg: dict):
        """Callback for each bar or tick pushed by the data source."""
        if self._stop_event.is_set():
            return

        # SL/TP callback
        if self.bar_callback:
            self.bar_callback(msg)

        # dispatch to bar or tick processing
        if 'open' in msg and 'high' in msg:
            self._process_bar(msg)
        elif 'price' in msg:
            self._process_tick(msg)

    def _process_bar(self, bar: dict):
        """Emit or group a 1m bar into the configured TF."""
        # 1m: emit directly
        if self.current_tf == '1m':
            self.socketio.emit('bar', bar)
            time.sleep(self._emit_delay)
            return

        # higher TF: group into fixed windows
        window_secs  = self.group_size * 60
        window_start = (bar['time'] // window_secs) * window_secs

        if self._current_group_start is None:
            self._current_group_start = window_start

        if window_start == self._current_group_start:
            self._1m_buffer.append(bar)
        else:
            # window closed: aggregate and emit
            tf_bar = self._aggregate_time_window(
                self._1m_buffer,
                self._current_group_start,
                window_secs
            )
            self.socketio.emit('bar', tf_bar)
            time.sleep(self._emit_delay)

            # reset buffer
            self._1m_buffer           = [bar]
            self._current_group_start = window_start

    def _process_tick(self, tick: dict):
        """Emit a live tick immediately."""
        self.socketio.emit('tick', tick)

    @staticmethod
    def _aggregate_time_window(
        bars: List[dict],
        window_start: int,
        window_secs: int
    ) -> dict:
        """Combine a list of 1m bars into one TF bar."""
        open_  = bars[0]['open']
        close_ = bars[-1]['close']
        high   = max(b['high'] for b in bars)
        low    = min(b['low']  for b in bars)
        volume = sum(b['volume'] for b in bars)
        pair   = bars[0]['pair']

        return {
            'time':   window_start + window_secs,
            'open':   open_,
            'high':   high,
            'low':    low,
            'close':  close_,
            'volume': volume,
            'pair':   pair
        }