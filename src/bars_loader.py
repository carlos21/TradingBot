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
        self._stop_at = None

        # grouping state
        self.current_tf           = '1m'
        self.group_size           = 1        # minutes for aggregation
        self._1m_buffer           = []
        self._current_group_start = None     # epoch-sec start of current window

        self._default_emit_delay = self._emit_delay   # ← remember normal speed
        self._fast_jump_mode  = False                 # ← when True we restore at end
        self._last_played_ts  = 0 # ← updated on every bar/tick

    def reset(self):
        """Reset state for a fresh scenario run."""
        self._last_played_ts = 0
        self._1m_buffer = []
        self._current_group_start = None
        self._from_time = 0
        self.streaming = False
        self._stop_event.clear()

    def set_timeframe(self, tf: str):
        print(f"[SET_TF] called with tf={tf}")

        # 1) stop existing replay thread (signal only)
        self._stop_event.set()

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

        # 4) reseed the buffer
        # FIX: Prefer raw bars from datasource (_bars) to ensure complete window history.
        # _played_bars might be truncated or incomplete upon resume.
        source_bars = getattr(self.data_source, "_bars", None)
        if source_bars is None:
            # Fallback for live sources or if _bars is private/unavailable
            source_bars = getattr(self.data_source, "_played_bars", [])

        # Filter for bars in the current window up to from_time
        # We include _from_time here. The duplicate check in _process_bar handles the overlap.
        buf = [b for b in source_bars if win_start <= b['time'] <= self._from_time]
        
        self._1m_buffer = buf
        self._current_group_start = win_start
        
        # 5) clear stop flag so start() can resume
        self._stop_event.clear()

        if buf:
            times = [b['time'] for b in buf]
            print(f"[SET_TF] seeded buffer: count={len(buf)}, "
                f"min={min(times)}, max={max(times)}, window_start={win_start}")
        else:
            print(f"[SET_TF] seeded buffer is EMPTY for window_start={win_start}")

        print("[SET_TF] ready to start() with new timeframe")

    def start(self, from_time: int = None, stop_at: int = None):
        """
        Begin or resume streaming.
        """
        # 1. Ensure any previous run is stopped
        self._stop_event.set()
        # Give a tiny moment for the old loop to see the flag and exit
        time.sleep(0.05) 
        
        if from_time is not None:
            self._from_time = from_time
            # FIX: Clear the buffer when seeking/starting from a specific time.
            # This ensures we don't duplicate data if we are restarting 
            # from the beginning of a window.
            self._1m_buffer = []
            self._current_group_start = None

        self._stop_at = stop_at

        if stop_at is not None:
            self._fast_jump_mode = True
            self._emit_delay = 0.0
        else:
            self._fast_jump_mode = False
            self._emit_delay = self._default_emit_delay

        self.streaming = True
        self._stop_event.clear()

        if hasattr(self.data_source, "_stop_event"):
            self.data_source._stop_event.clear()

        # 2. Start new task
        self.socketio.start_background_task(self._run_subscription, self._from_time)

    def pause(self):
        """
        Pause streaming.  
        - Signals our own loop to stop  
        - If the data source supports pause(), stop it too
        """
        # 1) stop our grouping & dispatch loop
        self._stop_event.set()

        self._stop_at = None

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
        # FIX: Update the 'Virtual Time' to the seek target immediately.
        # This ensures lines drawn right after seeking get the correct timestamp.
        self._last_played_ts      = from_time 

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
        
        stop_at = self._stop_at

        # SL/TP callback
        if self.bar_callback:
            self.bar_callback(msg)

        # dispatch to bar or tick processing
        if 'open' in msg and 'high' in msg:
            self._last_played_ts = msg['time']
            self._process_bar(msg)
        elif 'price' in msg:
            self._last_played_ts = msg['time']
            self._process_tick(msg)

        # check if we reached the end
        if stop_at is not None and 'time' in msg and msg['time'] >= stop_at:
            # avisar a la datasource que pare
            self._stop_event.set()
            if hasattr(self.data_source, '_stop_event'):
                self.data_source._stop_event.set()

            self.streaming = False
            self.socketio.emit('stream_status', {'playing': False})
            # razón especial para que el front sepa que fue “navegación de día”
            self.socketio.emit('stream_end', {'reason': 'day_end', 'stop_at': stop_at})
            return

    def _process_bar(self, bar: dict):
        """Handle one raw 1m bar: either emit immediately or buffer+aggregate."""
        # 1m: just emit
        if self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1:
            # print(f"[PROCESS_BAR] 🔹 1m — emitting raw bar time={bar['time']}")
            self.socketio.emit('bar', bar)
            time.sleep(self._emit_delay)
            return

        window_secs  = self.group_size * 60
        window_start = (bar['time'] // window_secs) * window_secs

        # print(f"[PROCESS_BAR] raw_time={bar['time']}, "
        #     f"window_start={window_start}, buf_len={len(self._1m_buffer)}")

        if self._current_group_start is None:
            self._current_group_start = window_start
            # print(f"[PROCESS_BAR] 🎬 new window at {window_start}")

        # same window → buffer it
        if window_start == self._current_group_start:
            if self._1m_buffer and self._1m_buffer[-1]['time'] == bar['time']:
                # ignore the duplicate pushed by subscribe right after seeding
                return
            self._1m_buffer.append(bar)
            # print(f"[PROCESS_BAR] ➕ buffering bar time={bar['time']} (buffer size={len(self._1m_buffer)})")
        else:
            # window closed → aggregate and emit (only if we have data)
            if self._1m_buffer:
                agg = self._aggregate_time_window(
                    self._1m_buffer,
                    self._current_group_start,
                    window_secs
                )
                # print(f"[PROCESS_BAR] 🔄 emitting aggregate for window {self._current_group_start}: "
                #   f"count={len(self._1m_buffer)}, agg_time={agg['time']}")
                self.socketio.emit('bar', agg)
                time.sleep(self._emit_delay)
            else:
                pass
                # print(f"[PROCESS_BAR] ⚠️ warning: buffer empty for window {self._current_group_start}, skipping aggregate")

            # reset for next window
            self._1m_buffer = [bar]
            self._current_group_start = window_start
            # print(f"[PROCESS_BAR] 🎬 started new window at {window_start}")    

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
            # FIX: Only flush the final aggregate if we reached EOF naturally.
            # If we are paused (_stop_event is set), DO NOT flush incomplete bars.
            if not self._stop_event.is_set():
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

            if self._fast_jump_mode:
                self._emit_delay = self._default_emit_delay
                self._fast_jump_mode = False

    # navigate to next day

    def _get_all_bars(self):
        """Return the full raw list from the underlying CSVDataSource/CombinedDataSource."""
        return getattr(self.data_source, "_bars", None)

    def _find_next_same_time_next_day(self, current_ts: int, days: int) -> int:
        """
        Given a current unix ts and a day offset (+1 or -1), find the bar in the CSV
        that is >= (current_ts + days*86400) (for +1) or <= (current_ts - 1) for -1.
        If not found, return the last/first bar available.
        """
        bars = self._get_all_bars()
        if not bars:
            # nothing better, just shift the timestamp
            return current_ts + days * 86400

        if days > 0:
            target = current_ts + days * 86400
            for b in bars:
                if b["time"] >= target:
                    return b["time"]
            # no bar after → stay at last
            return bars[-1]["time"]
        else:
            # days < 0 → go backwards
            target = current_ts + days * 86400
            prev = bars[0]["time"]
            for b in bars:
                if b["time"] > target:
                    return prev
                prev = b["time"]
            return prev
        
    def jump_day(self, direction: int = 1, fast: bool = True) -> int:
        """
        Jump +1 or -1 day from the last played bar and replay from there.
        - direction: +1 = next day, -1 = previous day
        - fast=True: replay with no delays, then restore normal speed
        Returns the unix timestamp we actually jumped to.
        """
        # 1) decide base time (prefer DS _played_bars, fall back to self._last_played_ts)
        base_ts = 0
        played = getattr(self.data_source, "_played_bars", None)
        if played:
            base_ts = played[-1]["time"]
        elif self._last_played_ts:
            base_ts = self._last_played_ts
        else:
            # no bars played yet → use current _from_time
            base_ts = self._from_time

        target_ts = self._find_next_same_time_next_day(base_ts, direction)

        # 2) prepare fast mode
        if fast:
            self._fast_jump_mode = True
            self._emit_delay = 0.0
        else:
            self._fast_jump_mode = False
            self._emit_delay = self._default_emit_delay

        # 3) reset and start from that point
        self.seek(target_ts)
        self.start(target_ts)

        return target_ts