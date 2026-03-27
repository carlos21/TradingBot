import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .combined_datasource import CombinedDataSource


@dataclass
class NinjaTraderConfig:
    pair: str = "NQ"
    account: str = ""  # NinjaTrader account name (e.g. "Sim101", "MyLiveAccount")


class NinjaTraderDataSource(CombinedDataSource):
    """
    Data source that receives bars and ticks from NinjaTrader via HTTP
    (Flask routes call the ingest_* methods directly).
    """

    def __init__(self, cfg: NinjaTraderConfig):
        self._cfg = cfg
        self._historical_bars: List[Dict] = []
        self._live = False

        # BarsLoader compat
        self._stop_event = threading.Event()
        self._callback: Optional[Callable[[Dict], None]] = None
        self._from_time: int = 0
        self._cb_lock = threading.Lock()

        # Tick-to-bar aggregation
        self._current_bar: Optional[Dict] = None
        self._last_emit_time: float = 0.0
        self._last_history_time: int = 0  # last bar time from NinjaTrader history

        # Direct callbacks — set by app_factory for live mode
        self.on_history_complete: Optional[Callable[[List[Dict]], None]] = None
        self.on_live_bar: Optional[Callable[[Dict], None]] = None
        self.on_before_refresh: Optional[Callable[[], None]] = None

        # Refresh state
        self._refreshing = False
        self._refresh_lock = threading.Lock()
        # Long-poll: NinjaTrader blocks on GET /api/nt/await_command,
        # Python signals it via this Event when a command is available.
        self._command_event = threading.Event()
        self._command_queue: deque = deque()

    # ---- Refresh API (long-poll: NinjaTrader hangs on GET, Python signals) ----

    def enqueue_command(self, cmd: Dict):
        """Add a command to the queue and wake the long-poll thread."""
        self._command_queue.append(cmd)
        self._command_event.set()

    def request_history_refresh(self, days: int = 1):
        """Signal NinjaTrader to resend history. Wakes the hanging await_command GET."""
        with self._refresh_lock:
            if self._refreshing:
                print("[NTDataSrc] Refresh already in progress, skipping", flush=True)
                return
            # Check if a refresh command is already queued
            for cmd in self._command_queue:
                if cmd.get("command") == "request_history":
                    print("[NTDataSrc] Refresh already pending, skipping", flush=True)
                    return
            self._refreshing = True
        self.enqueue_command({"command": "request_history", "days": days})
        print(f"[NTDataSrc] Refresh signaled ({days} days)", flush=True)

    def await_command(self, timeout: float = 30.0) -> Optional[Dict]:
        """Block until a command is available (called by NinjaTrader's hanging GET).
        Returns the command dict, or None on timeout."""
        self._command_event.wait(timeout=timeout)
        self._command_event.clear()
        try:
            cmd = self._command_queue.popleft()
        except IndexError:
            return None
        # Re-set the event if there are more commands queued
        if self._command_queue:
            self._command_event.set()
        return cmd

    def handle_refresh_start(self):
        """Called when NinjaTrader signals refresh_start — clears bars, resets state."""
        print("[NTDataSrc] Refresh start — clearing bars and resetting state", flush=True)
        if self.on_before_refresh:
            try:
                self.on_before_refresh()
            except Exception as e:
                print(f"[NTDataSrc] ERROR in on_before_refresh: {e}", flush=True)
                import traceback
                traceback.print_exc()
        self._historical_bars.clear()
        self._live = False
        self._current_bar = None
        self._last_history_time = 0

    # ---- public ingest API (called from Flask routes) -----------------------

    def _insert_live_bar_sorted(self, bar: Dict):
        """Insert a live bar into _historical_bars in sorted order by time.

        NinjaTrader sends completed bars via fire-and-forget HTTP, so they
        can arrive out of order.  We insert in the correct position and then
        check for actual gaps (missing minutes) which indicate NinjaTrader
        failed to send a bar.
        """
        from datetime import datetime, timezone
        import bisect

        new_time = bar["time"]

        # Check for duplicate
        if self._historical_bars:
            last_time = self._historical_bars[-1]["time"]
            if new_time == last_time:
                last_dt = datetime.fromtimestamp(last_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                print(f"[NTDataSrc] WARNING: Duplicate live bar at {last_dt}, skipping", flush=True)
                return False

            # Fast path: bar arrives in order (most common case)
            if new_time > last_time:
                self._historical_bars.append(bar)
            else:
                # Out-of-order arrival — insert in sorted position
                times = [b["time"] for b in self._historical_bars]
                idx = bisect.bisect_left(times, new_time)
                if idx < len(times) and times[idx] == new_time:
                    # Duplicate at an earlier position
                    dup_dt = datetime.fromtimestamp(new_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    print(f"[NTDataSrc] WARNING: Duplicate live bar at {dup_dt} (out-of-order), skipping", flush=True)
                    return False
                self._historical_bars.insert(idx, bar)
                new_dt = datetime.fromtimestamp(new_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                last_dt = datetime.fromtimestamp(last_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                print(f"[NTDataSrc] WARNING: Live bar {new_dt} arrived OUT OF ORDER (last was {last_dt}), inserted at correct position", flush=True)
        else:
            self._historical_bars.append(bar)

        # After inserting, check for gaps in the live region only
        # (from _last_history_time onward)
        self._check_live_region_for_gaps()
        return True

    def _check_live_region_for_gaps(self):
        """Check the live portion of _historical_bars for gaps.

        Only checks bars after _last_history_time (the end of NinjaTrader history).
        If a gap is found, it means NinjaTrader skipped a minute — crash.
        """
        from datetime import datetime, timezone

        if not self._last_history_time or len(self._historical_bars) < 2:
            return

        # Find where live bars start
        start_idx = None
        for i, b in enumerate(self._historical_bars):
            if b["time"] > self._last_history_time:
                start_idx = i
                break
        if start_idx is None or start_idx < 1:
            return

        # Check continuity from the last history bar through all live bars
        for i in range(start_idx, len(self._historical_bars)):
            prev_time = self._historical_bars[i - 1]["time"]
            curr_time = self._historical_bars[i]["time"]
            if curr_time != prev_time + 60:
                gap_minutes = (curr_time - prev_time) // 60
                prev_dt = datetime.fromtimestamp(prev_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                curr_dt = datetime.fromtimestamp(curr_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                # Don't crash yet — bars might still be in-flight (out-of-order HTTP).
                # Log prominently so we can see if gaps persist.
                print(
                    f"[NTDataSrc] ⚠ LIVE GAP: {gap_minutes}min gap between {prev_dt} and {curr_dt} "
                    f"(might be in-flight, will recheck on next bar)",
                    flush=True
                )

    def ingest_bars(self, bars: List[Dict]):
        """Add a batch of historical bars (before HISTORY_END)."""
        from datetime import datetime, timezone
        for raw in bars:
            bar = {
                "time": int(raw["time"]),
                "open": float(raw["open"]),
                "high": float(raw["high"]),
                "low": float(raw["low"]),
                "close": float(raw["close"]),
                "volume": int(raw.get("volume", 0)),
                "pair": raw.get("pair", self._cfg.pair),
            }
            self._historical_bars.append(bar)
            dt = datetime.fromtimestamp(bar["time"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            print(f"[NTDataSrc] 1m BAR ADDED (history): {dt} O={bar['open']} H={bar['high']} L={bar['low']} C={bar['close']} V={bar['volume']}", flush=True)

    def mark_history_complete(self):
        """Called when NinjaTrader signals HISTORY_END (initial or refresh)."""
        with self._refresh_lock:
            self._refreshing = False
        self._live = True
        if self._historical_bars:
            self._last_history_time = self._historical_bars[-1]["time"]
        print(f"[NTDataSrc] History complete ({len(self._historical_bars)} bars), last_time={self._last_history_time}, switching to live", flush=True)
        if self.on_history_complete:
            try:
                self.on_history_complete(self._historical_bars)
            except Exception as e:
                print(f"[NTDataSrc] ERROR in on_history_complete: {e}", flush=True)
                import traceback
                traceback.print_exc()

    def ingest_live_bar(self, raw: Dict):
        """Process a single live bar from NinjaTrader.

        Only COMPLETED bars (not partial) are added to _historical_bars.
        Partial bars are visual-only and never become source of truth.
        """
        if self._refreshing:
            return  # Drop live bars during refresh — refresh data covers this window

        from datetime import datetime, timezone
        bar = {
            "time": int(raw["time"]),
            "open": float(raw["open"]),
            "high": float(raw["high"]),
            "low": float(raw["low"]),
            "close": float(raw["close"]),
            "volume": int(raw.get("volume", 0)),
            "pair": raw.get("pair", self._cfg.pair),
        }
        if raw.get("partial"):
            bar["partial"] = True
        else:
            dt = datetime.fromtimestamp(bar["time"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            if self._live:
                # Live bar — insert in sorted order (HTTP can deliver out-of-order)
                added = self._insert_live_bar_sorted(bar)
                if not added:
                    return  # duplicate, skip
            else:
                # Pre-history bar — just append
                self._historical_bars.append(bar)
            print(f"[NTDataSrc] 1m BAR ADDED (live): {dt} O={bar['open']} H={bar['high']} L={bar['low']} C={bar['close']} V={bar['volume']} total_bars={len(self._historical_bars)}", flush=True)
        if self.on_live_bar:
            self.on_live_bar(bar)

    def ingest_tick(self, raw: Dict):
        """Process a single live tick — VISUAL ONLY.

        Ticks are NEVER added to _historical_bars.
        They ONLY drive the partial bar animation on the chart.
        Completed 1m bars come exclusively from NinjaTrader via /api/nt/bar.
        """
        tick_time = int(raw["time"])
        price = float(raw["price"])
        volume = int(raw.get("volume", 0))
        pair = raw.get("pair", self._cfg.pair)

        bar_time = (tick_time // 60) * 60

        # Reset on minute boundary
        if self._current_bar is not None and self._current_bar["time"] != bar_time:
            self._current_bar = None

        if self._current_bar is None:
            self._current_bar = {
                "time": bar_time,
                "open": price, "high": price, "low": price, "close": price,
                "volume": volume, "pair": pair,
            }
        else:
            self._current_bar["high"] = max(self._current_bar["high"], price)
            self._current_bar["low"] = min(self._current_bar["low"], price)
            self._current_bar["close"] = price
            self._current_bar["volume"] += volume

        # Partial bar update at most once per second (display only)
        now = time.monotonic()
        if now - self._last_emit_time >= 1.0:
            self._last_emit_time = now
            partial = dict(self._current_bar)
            partial["partial"] = True
            if self.on_live_bar:
                self.on_live_bar(partial)

    # ---- CombinedDataSource interface ---------------------------------------

    def load_historical_bars(self, timeframe: str = '1m', start_time: int = None) -> List[Dict]:
        bars = self._historical_bars
        if start_time is not None:
            bars = [b for b in bars if b['time'] >= start_time]

        if timeframe == '1m':
            return list(bars)

        # Aggregate into higher timeframes
        unit = timeframe[-1]
        num = int(timeframe[:-1])
        if unit == 'm':
            window_secs = num * 60
        elif unit == 'h':
            window_secs = num * 3600
        else:
            return list(bars)

        buckets: Dict[int, List[Dict]] = defaultdict(list)
        for bar in bars:
            win = (bar['time'] // window_secs) * window_secs
            buckets[win].append(bar)

        agg_bars = []
        for win in sorted(buckets):
            group = buckets[win]
            agg_bars.append({
                'time': win,
                'open': group[0]['open'],
                'high': max(b['high'] for b in group),
                'low': min(b['low'] for b in group),
                'close': group[-1]['close'],
                'volume': sum(b['volume'] for b in group),
                'pair': group[0]['pair'],
            })
        return agg_bars

    def subscribe(self, callback: Callable[[Dict], None], from_time: int = 0) -> None:
        """BarsLoader compat — blocks until pause()."""
        self._stop_event.clear()
        with self._cb_lock:
            self._callback = callback
            self._from_time = from_time
        self._stop_event.wait()

    def pause(self):
        self._stop_event.set()

    def shutdown(self):
        self._stop_event.set()
