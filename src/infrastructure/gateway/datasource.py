"""
ZeroMQ Data Source - Receives market data from trading platforms.

This module provides a CombinedDataSource implementation that receives
bars and ticks from trading platforms via ZeroMQ.
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum, auto

from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.utils.app_logger import ILogger

from src.config.models import DEFAULT_HISTORY_DAYS
from src.notifier import NoOpNotifier, Notifier

from .gateway import GatewayConfig, TradingGateway
from .protocol import MessageType

logger = logging.getLogger(__name__)


class DataSourceState(Enum):
    DISCONNECTED = auto()
    CONNECTED = auto()
    REFRESHING = auto()
    LIVE = auto()


class ZMQDataSource(CombinedDataSource):
    """
    Data source that receives market data from trading platforms via ZeroMQ.

    This provides a fast, reliable ZeroMQ-based data source for live trading.
    more efficient ZeroMQ implementation.

    Usage:
        from src.infrastructure.gateway import TradingGateway, ZMQDataSource

        gateway = TradingGateway()
        data_source = ZMQDataSource(gateway=gateway)

        # Use with app_factory
        wiring = create_app(
            data_source=data_source,
            live_mode=True,
            ...
        )

        gateway.start()

    Features:
    - Sub-second tick delivery (vs HTTP polling)
    - Guaranteed message ordering (ZMQ guarantees)
    - Automatic reconnection
    - Backpressure handling
    """

    def __init__(
        self,
        logger: ILogger,
        gateway: TradingGateway | None = None,
        gateway_config: GatewayConfig | None = None,
        pair: str = "MNQ",
        history_days: int = DEFAULT_HISTORY_DAYS,
        notifier: Notifier | None = None,
    ):
        """
        Initialize the ZMQ data source.

        Args:
            logger: Logger instance (required)
            gateway: Existing TradingGateway instance (or None to create one)
            gateway_config: Configuration for creating a new gateway
            pair: Trading pair symbol
        """
        self.logger = logger
        self.pair = pair
        self.history_days = history_days
        self._gateway = gateway
        self._gateway_config = gateway_config or GatewayConfig()
        self._owns_gateway = gateway is None

        # Data storage
        self._historical_bars: list[dict] = []
        self._bars_lock = threading.RLock()
        self._state = DataSourceState.DISCONNECTED
        self._last_history_time: int = 0

        # Buffer for live bars received during refresh
        self._refresh_buffer: list[dict] = []

        # Callbacks (set by app_factory)
        self.on_history_complete: Callable[[list[dict]], None] | None = None
        self.on_live_bar: Callable[[dict], None] | None = None
        self.on_before_refresh: Callable[[], None] | None = None

        # CombinedDataSource interface
        self._stop_event = threading.Event()
        self._callback: Callable[[dict], None] | None = None
        self._from_time: int = 0
        self._cb_lock = threading.Lock()

        # Live bar building from ticks (for partial bars)
        self._current_bar: dict | None = None
        self._last_emit_time: float = 0.0

        # Track native partial bar receipts so tick-derived partials don't
        # overwrite platform-native partial bars (which are the source of truth).
        self._last_native_partial_time: float = 0.0

        # Stats
        self._stats = {
            "ticks_received": 0,
            "bars_received": 0,
            "history_batches": 0,
        }

        # Health tracking
        self._duplicate_count = 0
        self._gap_count = 0

        # Gap detection threshold (seconds). For 1m bars, anything > 2 min is a hole.
        self._gap_threshold: int = 120

        # Retry counter for stale history auto-refresh
        self._max_stale_history_retries: int = 3
        self._stale_history_retry_count: int = 0

        # Delay before first history request to let NinjaTrader populate its cache
        self._history_request_delay_sec: float = 3.0
        self._pending_refresh_timer: threading.Timer | None = None

        # Notifier for alerts when bar stream dies
        self._notifier = notifier or NoOpNotifier()

        # Heartbeat: track last completed bar time and monitor for stalls
        self._last_completed_bar_time: float = 0.0
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop_event = threading.Event()
        self._heartbeat_alert_sent: bool = False
        self._heartbeat_check_interval_sec: float = 30.0
        self._heartbeat_alert_threshold_sec: float = 90.0

    def _ensure_gateway(self) -> TradingGateway:
        """Get or create the gateway."""
        if self._gateway is None:
            self._gateway = TradingGateway(
                self.logger,
                config=self._gateway_config,
                pair=self.pair,
            )
            self._owns_gateway = True
        return self._gateway

    # -------------------------------------------------------------------------
    # CombinedDataSource Interface
    # -------------------------------------------------------------------------

    def load_historical_bars(
        self,
        timeframe: str = "1m",
        start_time: int = None,
    ) -> list[dict]:
        """
        Load historical bars (synchronous - returns current cache).

        In live mode, historical bars are pushed from the platform
        via the HISTORY_BATCH message.
        """
        with self._bars_lock:
            bars = list(self._historical_bars)

        if start_time is not None:
            bars = [b for b in bars if b["time"] >= start_time]

        # Deduplicate by time (keep first occurrence) and ensure sorted order
        seen = set()
        unique_bars = []
        for b in bars:
            t = b["time"]
            if t not in seen:
                seen.add(t)
                unique_bars.append(b)

        if timeframe == "1m":
            return [dict(b.items()) for b in unique_bars]

        # Aggregate into higher timeframes
        return self._aggregate_bars(unique_bars, timeframe)

    def _aggregate_bars(self, bars: list[dict], timeframe: str) -> list[dict]:
        """Aggregate 1m bars into higher timeframes."""
        unit = timeframe[-1]
        num = int(timeframe[:-1])

        if unit == "m":
            window_secs = num * 60
        elif unit == "h":
            window_secs = num * 3600
        else:
            return list(bars)

        buckets: dict[int, list[dict]] = defaultdict(list)
        for bar in bars:
            win = (bar["time"] // window_secs) * window_secs
            buckets[win].append(bar)

        agg_bars = []
        for win in sorted(buckets):
            group = buckets[win]
            agg_bars.append({
                "time": win,
                "open": group[0]["open"],
                "high": max(b["high"] for b in group),
                "low": min(b["low"] for b in group),
                "close": group[-1]["close"],
                "volume": sum(b["volume"] for b in group),
                "pair": group[0]["pair"],
            })
        return agg_bars

    def subscribe(
        self,
        callback: Callable[[dict], None],
        from_time: int = 0,
    ) -> None:
        """
        Subscribe to live bars (Blocks until pause()).

        This is the BarsLoader compatibility interface.
        """
        self._stop_event.clear()
        with self._cb_lock:
            self._callback = callback
            self._from_time = from_time
        self._stop_event.wait()

    def pause(self) -> None:
        """Pause the subscription."""
        self._stop_event.set()

    def shutdown(self) -> None:
        """Shutdown the data source."""
        self._stop_event.set()
        if self._owns_gateway and self._gateway:
            self._gateway.stop()

    # -------------------------------------------------------------------------
    # Data Ingestion (Called by Gateway Callbacks)
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """Start receiving data (call after setting up callbacks)."""
        gateway = self._ensure_gateway()

        # Register data callbacks
        gateway.on(MessageType.TICK, self._on_tick)
        gateway.on(MessageType.BAR, self._on_bar)
        gateway.on(MessageType.PARTIAL_BAR, self._on_partial_bar)
        gateway.on(MessageType.HISTORY_BATCH, self._on_history_batch)
        gateway.on(MessageType.HISTORY_END, self._on_history_end)
        gateway.on(MessageType.REFRESH_START, self._on_refresh_start)

        # Register connection-state callback so ZMQDataSource manages its own lifecycle
        gateway.on_connection_change(self._on_gateway_connection_change)

        # Start the gateway
        gateway.start()

        # Start heartbeat monitor
        self._start_heartbeat_monitor()

        self.logger.info("ZMQDataSource started")

    def stop(self) -> None:
        """Stop receiving data."""
        self._stop_heartbeat_monitor()
        if self._owns_gateway and self._gateway:
            self._gateway.stop()
        self._state = DataSourceState.DISCONNECTED
        self.logger.info("ZMQDataSource stopped")

    def _start_heartbeat_monitor(self) -> None:
        """Start background thread that watches for stalled completed-bar stream."""
        self._heartbeat_stop_event.clear()
        self._heartbeat_alert_sent = False
        self._last_completed_bar_time = time.monotonic()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="ZMQ-HeartbeatMonitor",
            daemon=True,
        )
        self._heartbeat_thread.start()
        self.logger.info("[Heartbeat] Started monitoring completed-bar stream (alert after 90s)")

    def _stop_heartbeat_monitor(self) -> None:
        """Stop the heartbeat monitor thread."""
        if self._heartbeat_thread is not None:
            self._heartbeat_stop_event.set()
            self._heartbeat_thread.join(timeout=2.0)
            self._heartbeat_thread = None

    def _heartbeat_loop(self) -> None:
        """Background thread: alert if no completed bar received for too long."""
        while not self._heartbeat_stop_event.is_set():
            self._heartbeat_stop_event.wait(self._heartbeat_check_interval_sec)
            if self._heartbeat_stop_event.is_set():
                break

            # Only alert when we are in LIVE state (market should be streaming)
            if self._state != DataSourceState.LIVE:
                continue

            elapsed = time.monotonic() - self._last_completed_bar_time
            if elapsed >= self._heartbeat_alert_threshold_sec:
                if not self._heartbeat_alert_sent:
                    self._heartbeat_alert_sent = True
                    msg = (
                        f"🚨 ALERT: No completed bar received for {self.pair} "
                        f"in {int(elapsed)}s. Strategy is blind. "
                        f"Check NinjaTrader ZMQ connector."
                    )
                    self.logger.error(f"[Heartbeat] {msg}")
                    try:
                        self._notifier.send(msg)
                    except Exception as e:
                        self.logger.error(f"[Heartbeat] FAILED to send notification: {type(e).__name__}: {e}")
            else:
                # Reset alert flag once bars resume
                if self._heartbeat_alert_sent:
                    self._heartbeat_alert_sent = False
                    self.logger.info(f"[Heartbeat] Completed-bar stream resumed for {self.pair}")

    def _on_tick(self, payload: dict) -> None:
        """Handle incoming tick."""
        self._stats["ticks_received"] += 1

        # Build partial bar from tick
        tick_time = int(payload["time"])
        price = float(payload["price"])
        volume = int(payload.get("volume", 0))

        bar_time = (tick_time // 60) * 60

        # Reset on minute boundary
        if self._current_bar is not None and self._current_bar["time"] != bar_time:
            self._current_bar = None

        if self._current_bar is None:
            self._current_bar = {
                "time": bar_time,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "pair": payload.get("pair", self.pair),
            }
        else:
            self._current_bar["high"] = max(self._current_bar["high"], price)
            self._current_bar["low"] = min(self._current_bar["low"], price)
            self._current_bar["close"] = price
            self._current_bar["volume"] += volume

        # Emit partial bar at most once per second, but only if the platform
        # is not already sending native partial bars (which are the source of truth).
        now = time.monotonic()
        if now - self._last_emit_time >= 1.0:
            self._last_emit_time = now
            # Skip tick-derived partial if we received a native partial recently.
            # This prevents the chart from flickering between tick-aggregated values
            # and the platform's true BarsSeries values.
            if now - self._last_native_partial_time < 2.0:
                return
            partial = dict(self._current_bar)
            partial["partial"] = True
            if self.on_live_bar:
                self.on_live_bar(partial)

    def _on_bar(self, payload: dict) -> None:
        """Handle completed bar from platform."""
        self._stats["bars_received"] += 1

        # DEBUG: Log every bar for the first 100 live bars, then every 50th
        is_live = self._state == DataSourceState.LIVE
        if is_live:
            if self._stats["bars_received"] <= 100 or self._stats["bars_received"] % 50 == 0:
                self.logger.info(
                    f"[LIVE BAR #{self._stats['bars_received']}] "
                    f"time={payload.get('time')} close={payload.get('close')} pair={payload.get('pair', self.pair)}"
                )

        if is_live:
            self._last_completed_bar_time = time.monotonic()
            # Reset alert flag when a bar arrives
            if self._heartbeat_alert_sent:
                self._heartbeat_alert_sent = False
                self.logger.info(f"[Heartbeat] Completed-bar stream resumed for {self.pair}")

        bar = {
            "time": int(payload["time"]),
            "open": float(payload["open"]),
            "high": float(payload["high"]),
            "low": float(payload["low"]),
            "close": float(payload["close"]),
            "volume": int(payload.get("volume", 0)),
            "pair": payload.get("pair", self.pair),
        }

        if self._state == DataSourceState.REFRESHING:
            self._refresh_buffer.append(bar)
            return  # Buffer live bars during refresh

        inserted_idx = -1
        # Insert in sorted order (platform should send in order, but be safe)
        with self._bars_lock:
            if not self._historical_bars or bar["time"] > self._historical_bars[-1]["time"]:
                self._historical_bars.append(bar)
                inserted_idx = len(self._historical_bars) - 1
            else:
                # Out of order - insert correctly
                times = [b["time"] for b in self._historical_bars]
                idx = bisect.bisect_left(times, bar["time"])
                if idx < len(times) and times[idx] == bar["time"]:
                    self._duplicate_count += 1
                    self.logger.warning(f"Duplicate bar at time {bar['time']} (total={self._duplicate_count})")
                    return
                self._historical_bars.insert(idx, bar)
                inserted_idx = idx
                self.logger.warning(f"Bar out of order: inserted at index {idx}")

            # Gap detection: check neighbors of the inserted bar
            if inserted_idx > 0:
                self._detect_gap(
                    self._historical_bars[inserted_idx - 1]["time"],
                    bar["time"],
                    "LIVE" if self._state == DataSourceState.LIVE else "INGEST"
                )
            if inserted_idx < len(self._historical_bars) - 1:
                self._detect_gap(
                    bar["time"],
                    self._historical_bars[inserted_idx + 1]["time"],
                    "LIVE" if self._state == DataSourceState.LIVE else "INGEST"
                )

        if self.on_live_bar:
            self.on_live_bar(bar)

    def _on_partial_bar(self, payload: dict) -> None:
        """Handle partial bar from platform."""
        self._last_native_partial_time = time.monotonic()
        if self.on_live_bar:
            partial = dict(payload)
            partial["partial"] = True
            self.on_live_bar(partial)

    def _on_history_batch(self, payload: dict) -> None:
        """Handle batch of historical bars."""
        self._stats["history_batches"] += 1

        bars = payload.get("bars", [])
        payload.get("days", 1)
        pair = payload.get("pair", self.pair)

        new_bars = []
        for raw in bars:
            bar = {
                "time": int(raw["time"]),
                "open": float(raw["open"]),
                "high": float(raw["high"]),
                "low": float(raw["low"]),
                "close": float(raw["close"]),
                "volume": int(raw.get("volume", 0)),
                "pair": raw.get("pair", self.pair),
            }
            new_bars.append(bar)

        with self._bars_lock:
            existing_times = {b["time"] for b in self._historical_bars}
            added = 0
            for bar in new_bars:
                if bar["time"] not in existing_times:
                    self._historical_bars.append(bar)
                    existing_times.add(bar["time"])
                    added += 1
            if added > 0 and len(self._historical_bars) > 1:
                self._historical_bars.sort(key=lambda b: b["time"])

        self.logger.info(f"RECV: history_batch | pair={pair} | bars={len(new_bars)} | unique_added={added} | total_cached={len(self._historical_bars)}")

    def _detect_gap(self, prev_time: int, curr_time: int, context: str) -> None:
        """Log a warning if there is a gap between two bar timestamps."""
        gap = curr_time - prev_time
        if gap > self._gap_threshold:
            self._gap_count += 1
            dt_prev = datetime.fromtimestamp(prev_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            dt_curr = datetime.fromtimestamp(curr_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            self.logger.warning(
                f"🕳️  GAP DETECTED [{context}]: {gap}s hole between {dt_prev} and {dt_curr} "
                f"({gap // 60}m {gap % 60}s)"
            )

    def _scan_for_gaps(self, bars: list[dict], context: str) -> int:
        """Scan a list of bars for gaps and log warnings. Returns total gap count."""
        if len(bars) < 2:
            return 0
        gap_count = 0
        for i in range(1, len(bars)):
            gap = bars[i]["time"] - bars[i - 1]["time"]
            if gap > self._gap_threshold:
                gap_count += 1
                if gap_count <= 5:
                    self._detect_gap(bars[i - 1]["time"], bars[i]["time"], context)
        if gap_count > 5:
            self.logger.warning(f"🕳️  GAP DETECTED [{context}]: ... and {gap_count - 5} more gap(s)")
        return gap_count

    def _on_history_end(self, _payload: dict = None) -> None:
        """Handle end of historical data."""
        with self._bars_lock:
            if self._historical_bars:
                self._last_history_time = self._historical_bars[-1]["time"]
            bars_copy = list(self._historical_bars)

            # Scan loaded history for gaps so we know if the source already had holes
            gap_count = self._scan_for_gaps(self._historical_bars, "HISTORY")

        # NinjaTrader's BarsRequest(DateTime, DateTime) can return partial cached
        # data on the first call after connection. If the last bar is far behind
        # now, re-request a refresh instead of switching to LIVE with stale data.
        if bars_copy:
            last_bar_time = bars_copy[-1]["time"]
            now = time.time()
            gap_to_now = now - last_bar_time
            if gap_to_now > 600:  # > 10 minutes stale
                self._stale_history_retry_count += 1
                if self._stale_history_retry_count <= self._max_stale_history_retries:
                    dt_str = datetime.fromtimestamp(last_bar_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    self.logger.warning(
                        f"🕳️  History ends {gap_to_now:.0f}s ago ({dt_str} UTC), "
                        f"re-requesting refresh (attempt {self._stale_history_retry_count}/{self._max_stale_history_retries})..."
                    )
                    self._state = DataSourceState.CONNECTED
                    self.request_refresh()
                    return
                else:
                    self.logger.error(
                        f"🕳️  History still ends {gap_to_now:.0f}s ago after "
                        f"{self._max_stale_history_retries} refresh attempts. "
                        f"Proceeding with stale data — large gap warnings expected."
                    )

        self._state = DataSourceState.LIVE
        self._stale_history_retry_count = 0
        # Reset heartbeat baseline so the first live bar has a full grace period
        self._last_completed_bar_time = time.monotonic()

        self.logger.info(f"History complete: {len(bars_copy)} bars cached, switching to LIVE mode")
        if gap_count > 0:
            self.logger.warning(f"🕳️  HISTORY SCAN: {gap_count} total gap(s) detected in {len(bars_copy)} bars")

        if self.on_history_complete:
            try:
                self.on_history_complete(bars_copy)
            except Exception as e:
                self.logger.error(f"Error in on_history_complete: {e}")

        # Flush any live bars that arrived during the refresh
        if self._refresh_buffer:
            buffered_count = len(self._refresh_buffer)
            self.logger.info(f"Flushing {buffered_count} live bars buffered during refresh")
            for buffered_bar in self._refresh_buffer:
                self._on_bar(buffered_bar)
            self._refresh_buffer.clear()

    def _on_refresh_start(self, _payload: dict = None) -> None:
        """Handle refresh start - clear recent data."""
        if self._state == DataSourceState.REFRESHING:
            self.logger.info("Refresh start ignored: already refreshing")
            return

        self.logger.info("Refresh start - clearing recent data")

        if self.on_before_refresh:
            try:
                self.on_before_refresh()
            except Exception as e:
                self.logger.error(f"Error in on_before_refresh: {e}")

        # Keep bars older than 1 day
        cutoff = int(time.time()) - 86400
        with self._bars_lock:
            preserved = [b for b in self._historical_bars if b["time"] < cutoff]
            removed = len(self._historical_bars) - len(preserved)
            self._historical_bars = preserved
            self._last_history_time = preserved[-1]["time"] if preserved else 0

        self._state = DataSourceState.REFRESHING
        self._current_bar = None
        self._refresh_buffer.clear()

        self.logger.info(f"Refresh start: preserved {len(preserved)} historical bars, removed {removed} recent bars")

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def request_refresh(self, days: int = None) -> None:
        """Request historical data refresh from platform."""
        if self._state == DataSourceState.REFRESHING:
            self.logger.info("Refresh request ignored: already refreshing")
            return
        if self._state == DataSourceState.DISCONNECTED:
            self.logger.info("Refresh request ignored: platform not connected")
            return
        gateway = self._ensure_gateway()
        gateway.send_refresh_request(days=days or self.history_days)

    @property
    def is_live(self) -> bool:
        """Check if receiving live data."""
        return self._state == DataSourceState.LIVE

    @property
    def state(self) -> DataSourceState:
        """Current data source state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Check if connected to platform."""
        return self._gateway is not None and self._gateway.is_connected

    def _cancel_pending_refresh_timer(self) -> None:
        """Cancel any pending delayed refresh request."""
        if self._pending_refresh_timer is not None:
            self._pending_refresh_timer.cancel()
            self._pending_refresh_timer = None

    def _do_delayed_refresh(self) -> None:
        """Execute the delayed refresh request."""
        self._pending_refresh_timer = None
        if self._state == DataSourceState.DISCONNECTED:
            self.logger.info("Delayed refresh aborted: platform disconnected")
            return
        self.logger.info("Requesting historical data refresh after delay")
        self.request_refresh()

    def on_platform_connected(self) -> None:
        """Called when the platform connects. Auto-request refresh if needed."""
        if self._state == DataSourceState.DISCONNECTED:
            self._state = DataSourceState.CONNECTED
            self._stale_history_retry_count = 0
            delay = self._history_request_delay_sec
            self.logger.info(f"Platform connected, requesting historical data refresh in {delay}s")
            self._cancel_pending_refresh_timer()
            self._pending_refresh_timer = threading.Timer(delay, self._do_delayed_refresh)
            self._pending_refresh_timer.start()
        else:
            self.logger.debug(f"Platform connected ignored: state={self._state.name}")

    def on_platform_disconnected(self) -> None:
        """Called when the platform disconnects."""
        if self._state != DataSourceState.DISCONNECTED:
            self._state = DataSourceState.DISCONNECTED
            self._cancel_pending_refresh_timer()
            self.logger.info("Platform disconnected")

    def _on_gateway_connection_change(self, connected: bool) -> None:
        """Internal callback registered with the gateway."""
        if connected:
            self.on_platform_connected()
        else:
            self.on_platform_disconnected()

    @property
    def gateway(self) -> TradingGateway | None:
        """Access the underlying TradingGateway for advanced configuration."""
        return self._gateway

    def get_health(self) -> dict:
        """Get current stream health snapshot."""
        with self._bars_lock:
            bars_cached = len(self._historical_bars)
            last_bar_time = self._historical_bars[-1]["time"] if self._historical_bars else None

        heartbeat_age = time.monotonic() - self._last_completed_bar_time if self._last_completed_bar_time > 0 else None

        return {
            "state": self._state.name,
            "bars_cached": bars_cached,
            "last_bar_time": last_bar_time,
            "heartbeat_age_sec": round(heartbeat_age, 1) if heartbeat_age is not None else None,
            "duplicate_count": self._duplicate_count,
            "gap_count": self._gap_count,
            "ticks_received": self._stats["ticks_received"],
            "bars_received": self._stats["bars_received"],
            "history_batches": self._stats["history_batches"],
            "platform_connected": self.is_connected,
            "pair": self.pair,
        }

    @property
    def stats(self) -> dict[str, int]:
        """Get reception statistics."""
        return dict(self._stats)
