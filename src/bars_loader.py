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
        print(f"[SET_TF] called with tf={tf}")

        # 1) stop existing replay thread
        if self._thread and self._thread.is_alive():
            print("[SET_TF] stopping existing thread")
            self._stop_event.set()
            self._thread.join()

        # 2) parse the new TF
        unit = tf[-1]
        num  = int(tf[:-1])
        if unit == 'm':
            self.group_size = max(1, num)
        elif unit == 'h':
            self.group_size = max(1, num * 60)
        else:
            raise ValueError(f"Unsupported timeframe '{tf}'")

        self.current_tf = tf
        print(f"[SET_TF] new group_size={self.group_size} ({tf})")

        # 3) compute the start of the current window
        window_secs = self.group_size * 60
        win_start   = (self._from_time // window_secs) * window_secs
        print(f"[SET_TF] current from_time={self._from_time}, window_start={win_start}")

        # 4) reseed the buffer with already-played bars in that window
        played = self.data_source._played_bars
        buf = [b for b in played if win_start <= b['time'] <= self._from_time]
        self._1m_buffer = buf
        self._current_group_start = win_start
        print(f"[SET_TF] seeded _1m_buffer with {len(buf)} bars from data_source._played_bars")

        # 5) clear stop flag so start() can resume
        self._stop_event.clear()
        print("[SET_TF] ready to start() with new timeframe")

        if buf:
            times = [b['time'] for b in buf]
            print(f"[SET_TF] seeded buffer: count={len(buf)}, "
                f"min={min(times)}, max={max(times)}, window_start={win_start}")
        else:
            print(f"[SET_TF] seeded buffer is EMPTY for window_start={win_start}")

        print("[SET_TF] ready to start() with new timeframe")

    def start(self, from_time: int = None):
        """
        Begin or resume streaming.
        `from_time` is passed to the data-source.subscribe via our wrapper.
        """
        if from_time is not None:
            self._from_time = from_time

        self.streaming = True
        self._stop_event.clear()

        if hasattr(self.data_source, "_stop_event"):
            self.data_source._stop_event.clear()

        # ⬇️ use our wrapper so we can flush + emit stream_end on completion
        self.socketio.start_background_task(self._run_subscription, self._from_time)

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
        # 🔚 handle end-of-stream sentinel FIRST so we never miss it
        if isinstance(msg, dict) and msg.get('_end'):
            self.streaming = False
            self.socketio.emit('stream_status', {'playing': False})
            self.socketio.emit('stream_end', {'ok': True})
            return
    
        """Callback for each bar or tick pushed by the data source."""
        if self._stop_event.is_set():
            return

        # 🔚 end sentinel
        if isinstance(msg, dict) and msg.get('_end'):
            self.streaming = False
            self.socketio.emit('stream_status', {'playing': False})
            self.socketio.emit('stream_end', {'ok': True})
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
        """Handle one raw 1m bar: either emit immediately or buffer+aggregate."""
        # 1m: just emit
        if self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1:
            print(f"[PROCESS_BAR] 🔹 1m — emitting raw bar time={bar['time']}")
            self.socketio.emit('bar', bar)
            time.sleep(self._emit_delay)
            return

        window_secs  = self.group_size * 60
        window_start = (bar['time'] // window_secs) * window_secs

        print(f"[PROCESS_BAR] raw_time={bar['time']}, "
            f"window_start={window_start}, buf_len={len(self._1m_buffer)}")

        if self._current_group_start is None:
            self._current_group_start = window_start
            print(f"[PROCESS_BAR] 🎬 new window at {window_start}")

        # same window → buffer it
        if window_start == self._current_group_start:
            self._1m_buffer.append(bar)
            print(f"[PROCESS_BAR] ➕ buffering bar time={bar['time']} (buffer size={len(self._1m_buffer)})")
        else:
            # window closed → aggregate and emit (only if we have data)
            if self._1m_buffer:
                agg = self._aggregate_time_window(
                    self._1m_buffer,
                    self._current_group_start,
                    window_secs
                )
                print(f"[PROCESS_BAR] 🔄 emitting aggregate for window {self._current_group_start}: "
                  f"count={len(self._1m_buffer)}, agg_time={agg['time']}")
                self.socketio.emit('bar', agg)
                time.sleep(self._emit_delay)
            else:
                print(f"[PROCESS_BAR] ⚠️ warning: buffer empty for window {self._current_group_start}, skipping aggregate")

            # reset for next window
            self._1m_buffer = [bar]
            self._current_group_start = window_start
            print(f"[PROCESS_BAR] 🎬 started new window at {window_start}")    

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
    
    def _run_subscription(self, from_time: int):
        """
        Wrapper executed in the Socket.IO background task:
        - Runs the data source subscription (pushes bars into _handle_message)
        - Flushes the last aggregated TF bar (if any)
        - Emits 'stream_end' so external automation can know we're done
        """
        try:
            # blocks until data_source.subscribe finishes
            self.data_source.subscribe(self._handle_message, from_time)
        finally:
            # flush final aggregate if TF > 1m and we still have buffered bars
            try:
                if not (self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1):
                    if self._1m_buffer:
                        window_secs = self.group_size * 60
                        agg = self._aggregate_time_window(self._1m_buffer, self._current_group_start, window_secs)
                        self.socketio.emit('bar', agg)
                        # small pacing so UI can render before end signal
                        time.sleep(self._emit_delay)
            except Exception:
                # don't let flush errors block end signal
                pass

            self.streaming = False
            reason = 'paused' if self._stop_event.is_set() else 'eof'
            self.socketio.emit('stream_end', {'reason': reason})