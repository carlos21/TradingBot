from typing import Callable, List
from dataclasses import dataclass
from flask_socketio import SocketIO
import threading
import time

from src.data_sources.combined_datasource import CombinedDataSource

@dataclass
class LoaderConfig:
    initial_start: int
    initial_end:   int

class BarsLoader:
    def __init__(
        self,
        data_source: CombinedDataSource,
        socketio: SocketIO,
        bar_callback: Callable[[dict], None] = None,
        stream_end_callback: Callable[[float, float], None] = None,
        bars_per_second: float = 10.0
    ):
        self.data_source     = data_source
        self.socketio        = socketio
        self.bar_callback    = bar_callback
        self.stream_end_callback = stream_end_callback
        self.bars_per_second = bars_per_second
        self._emit_delay     = 1.0 / bars_per_second

        self._from_time    = 0
        self.streaming     = False
        self._stop_event   = threading.Event()
        self._thread       = None
        self._stop_at = None
        self._reached_stop_at = False

        self.current_tf           = '1m'
        self.group_size           = 1
        self._1m_buffer           = []
        self._current_group_start = None

        self._default_emit_delay = self._emit_delay
        self._fast_jump_mode  = False
        self._last_played_ts  = 0
        self._last_bar_close  = 0
        self._step_mode       = False
        self.live_mode        = False

    def reset(self):
        self._last_played_ts = 0
        self._last_bar_close = 0
        self._1m_buffer = []
        self._current_group_start = None
        self._from_time = 0
        self.streaming = False
        self._stop_event.clear()
        self._reached_stop_at = False
        self._step_mode = False

    def set_timeframe(self, tf: str):
        if not self.live_mode:
            self._stop_event.set()

        unit = tf[-1]
        num  = int(tf[:-1])
        if unit == 'm': self.group_size = max(1, num)
        elif unit == 'h': self.group_size = max(1, num * 60)
        else: raise ValueError(f"Unsupported timeframe '{tf}'")

        self.current_tf = tf
        window_secs = self.group_size * 60
        win_start   = (self._from_time // window_secs) * window_secs

        source_bars = self._get_source_bars()

        buf = [b for b in source_bars if win_start <= b['time'] <= self._from_time]
        self._1m_buffer = buf
        self._current_group_start = win_start

        if not self.live_mode:
            self._stop_event.clear()
        self._reached_stop_at = False

    def start(self, from_time: int = None, stop_at: int = None):
        self._stop_event.set()
        time.sleep(0.05) 
        
        if from_time is not None:
            self._from_time = from_time
            self._1m_buffer = []
            self._current_group_start = None

        self._stop_at = stop_at
        self._reached_stop_at = False

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

        self.socketio.start_background_task(self._run_subscription, self._from_time)

    def pause(self):
        if not self.live_mode:
            self._stop_event.set()
        self._stop_at = None
        self._step_mode = False
        if not self.live_mode and hasattr(self.data_source, 'pause'):
            try: self.data_source.pause()
            except Exception: pass
        self.streaming = False

    def seek(self, from_time: int):
        self._from_time           = from_time
        self._1m_buffer           = []
        self._current_group_start = None
        self._last_played_ts      = from_time

    def step(self):
        """Advance exactly one bar (or one aggregated candle) then pause."""
        self._stop_event.set()
        time.sleep(0.05)

        self._step_mode = True
        self._stop_at = None
        self._reached_stop_at = False
        self._fast_jump_mode = False
        self._emit_delay = self._default_emit_delay

        self.streaming = True
        self._stop_event.clear()

        if hasattr(self.data_source, "_stop_event"):
            self.data_source._stop_event.clear()

        self.socketio.start_background_task(self._run_subscription, self._from_time)

    def _handle_message(self, msg: dict):
        if isinstance(msg, dict) and msg.get('_end'):
            self.streaming = False
            print(f"[BarsLoader] _END message received! last_bar_close={self._last_bar_close}, last_played_ts={self._last_played_ts}")
            print(f"[BarsLoader] stream_end_callback exists: {self.stream_end_callback is not None}")
            if self.stream_end_callback and self._last_played_ts > 0:
                print(f"[BarsLoader] Calling stream_end_callback NOW!")
                self.stream_end_callback(self._last_bar_close, self._last_played_ts)
            else:
                print(f"[BarsLoader] NOT calling callback: callback={self.stream_end_callback is not None}, last_ts={self._last_played_ts}")
            self.socketio.emit('stream_status', {'playing': False})
            self.socketio.emit('stream_end', {'ok': True})
            return

        if not self.live_mode and self._stop_event.is_set(): return

        is_partial = msg.get('partial', False)

        if self.live_mode and not is_partial and 'open' in msg:
            from datetime import datetime, timezone
            ts = msg.get('time', 0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S')
            print(f"[BarsLoader] PROCESSING completed bar time={dt} tf={self.current_tf} buf_len={len(self._1m_buffer)} group_start={self._current_group_start}", flush=True)

        # Partial bars: emit to frontend for display only, skip strategy
        if is_partial:
            self._last_played_ts = msg['time']
            self._last_bar_close = msg.get('close', 0)
            bar_for_emit = {k: v for k, v in msg.items() if k != 'partial'}

            # For higher timeframes, merge partial tick data with buffered
            # 1m bars so the chart shows the correct aggregated candle
            if not (self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1):
                window_secs = self.group_size * 60
                window_start = (bar_for_emit['time'] // window_secs) * window_secs
                bar_for_emit['time'] = window_start

                # Combine buffered completed 1m bars + this partial tick
                if self._1m_buffer:
                    bar_for_emit = {
                        'time': window_start,
                        'open': self._1m_buffer[0]['open'],
                        'high': max(max(b['high'] for b in self._1m_buffer), bar_for_emit['high']),
                        'low': min(min(b['low'] for b in self._1m_buffer), bar_for_emit['low']),
                        'close': bar_for_emit['close'],
                        'volume': sum(b['volume'] for b in self._1m_buffer) + bar_for_emit.get('volume', 0),
                        'pair': bar_for_emit.get('pair', self._1m_buffer[0]['pair']),
                    }

            self.socketio.emit('bar', bar_for_emit)
            return

        if self.bar_callback: self.bar_callback(msg)

        if 'open' in msg and 'high' in msg:
            self._last_played_ts = msg['time']
            self._last_bar_close = msg.get('close', 0)
            self._process_bar(msg)
        elif 'price' in msg:
            self._last_played_ts = msg['time']
            self._process_tick(msg)

        if self._stop_at is not None and 'time' in msg and msg['time'] >= self._stop_at:
            self._reached_stop_at = True
            self._stop_event.set()
            if hasattr(self.data_source, '_stop_event'):
                self.data_source._stop_event.set()

            self.streaming = False
            print(f"[BarsLoader] STOP_AT reached! msg_time={msg['time']}, stop_at={self._stop_at}")
            print(f"[BarsLoader] stream_end_callback exists: {self.stream_end_callback is not None}")
            if self.stream_end_callback:
                close_price = msg.get('close', self._last_bar_close)
                print(f"[BarsLoader] Calling stream_end_callback with close={close_price}, time={msg['time']}")
                self.stream_end_callback(close_price, msg['time'])
            else:
                print(f"[BarsLoader] NO callback set, trades will remain open")
            self.socketio.emit('stream_status', {'playing': False})
            self.socketio.emit('stream_end', {'reason': 'day_end', 'stop_at': self._stop_at})
            return

    def _stop_after_step(self):
        self._step_mode = False
        self._stop_event.set()
        self.streaming = False
        self.socketio.emit('stream_status', {'playing': False})

    def _process_bar(self, bar: dict):
        if self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1:
            if self.live_mode:
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(bar.get('time', 0), tz=timezone.utc).strftime('%H:%M:%S')
                print(f"[BarsLoader] EMIT 1m bar time={dt} C={bar.get('close')}", flush=True)
            self.socketio.emit('bar', bar)
            time.sleep(self._emit_delay)
            if self._step_mode:
                self._stop_after_step()
            return

        window_secs  = self.group_size * 60
        window_start = (bar['time'] // window_secs) * window_secs

        if self._current_group_start is None:
            self._current_group_start = window_start

        if window_start == self._current_group_start:
            if self._1m_buffer and self._1m_buffer[-1]['time'] == bar['time']: return
            self._1m_buffer.append(bar)
        else:
            if self._1m_buffer:
                agg = self._aggregate_time_window(self._1m_buffer, self._current_group_start, window_secs)
                self.socketio.emit('bar', agg)
                time.sleep(self._emit_delay)
                if self._step_mode:
                    self._stop_after_step()
            self._1m_buffer = [bar]
            self._current_group_start = window_start

    def _process_tick(self, tick: dict):
        self.socketio.emit('tick', tick)

    @staticmethod
    def _aggregate_time_window(bars: List[dict], window_start: int, window_secs: int) -> dict:
        open_  = bars[0]['open']
        close_ = bars[-1]['close']
        high   = max(b['high'] for b in bars)
        low    = min(b['low']  for b in bars)
        volume = sum(b['volume'] for b in bars)
        pair   = bars[0]['pair']
        return {
            'time':   window_start,
            'open':   open_, 'high': high, 'low': low, 'close': close_,
            'volume': volume, 'pair': pair
        }
    
    def _run_subscription(self, from_time: int):
        try:
            self.data_source.subscribe(self._handle_message, from_time)
        finally:
            # Flush if we reached EOF naturally OR if we hit the stop_at target
            should_flush = (not self._stop_event.is_set()) or self._reached_stop_at

            if should_flush:
                try:
                    if not (self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1):
                        if self._1m_buffer:
                            window_secs = self.group_size * 60
                            agg = self._aggregate_time_window(self._1m_buffer, self._current_group_start, window_secs)
                            self.socketio.emit('bar', agg)
                            time.sleep(self._emit_delay)
                except Exception:
                    pass

            self.streaming = False
            reason = 'paused' if (self._stop_event.is_set() and not self._reached_stop_at) else 'eof'
            self.socketio.emit('stream_end', {'reason': reason})

            if self._fast_jump_mode:
                self._emit_delay = self._default_emit_delay
                self._fast_jump_mode = False

    def _get_source_bars(self):
        for attr in ("_bars", "_played_bars", "_historical_bars"):
            bars = getattr(self.data_source, attr, None)
            if bars is not None:
                return bars
        return []

    def _get_all_bars(self):
        return self._get_source_bars() or None

    def _find_next_same_time_next_day(self, current_ts: int, days: int) -> int:
        bars = self._get_all_bars()
        if not bars: return current_ts + days * 86400
        target = current_ts + days * 86400
        if days > 0:
            for b in bars:
                if b["time"] >= target: return b["time"]
            return bars[-1]["time"]
        else:
            prev = bars[0]["time"]
            for b in bars:
                if b["time"] > target: return prev
                prev = b["time"]
            return prev
        
    def jump_day(self, direction: int = 1, fast: bool = True) -> int:
        if self.live_mode:
            return self._last_played_ts or self._from_time

        base_ts = 0
        played = getattr(self.data_source, "_played_bars", None)
        if played: base_ts = played[-1]["time"]
        elif self._last_played_ts: base_ts = self._last_played_ts
        else: base_ts = self._from_time

        target_ts = self._find_next_same_time_next_day(base_ts, direction)

        if fast:
            self._fast_jump_mode = True
            self._emit_delay = 0.0
        else:
            self._fast_jump_mode = False
            self._emit_delay = self._default_emit_delay

        self.seek(target_ts)
        self.start(target_ts)
        return target_ts