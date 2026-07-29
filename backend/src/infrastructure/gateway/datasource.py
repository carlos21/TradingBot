"""
ZeroMQ Data Source - Receives market data from trading platforms.

This module provides a CombinedDataSource implementation that receives
bars and ticks from trading platforms via ZeroMQ.
"""

from __future__ import annotations

import bisect
import contextlib
import logging
import math
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum, auto
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from src.config.models import DEFAULT_HISTORY_HOURS
from src.domain.parity import IMarketClosureFilter
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.notifier import NoOpNotifier, Notifier
from src.utils.app_logger import ILogger

from .gateway import GatewayConfig, TradingGateway
from .protocol import MessageType

if TYPE_CHECKING:
    from src.application.live_readiness.readiness_monitor import ReadinessMonitor
    from src.domain.models import Instrument

logger = logging.getLogger(__name__)


class DataSourceState(Enum):
    DISCONNECTED = auto()
    CONNECTED = auto()
    REFRESHING = auto()
    STREAMING = auto()


class ZMQDataSource(CombinedDataSource):
    """
    Data source that receives market data from trading platforms via ZeroMQ.

    This provides a fast, reliable ZeroMQ-based data source for live trading.
    more efficient ZeroMQ implementation.

    There is no "default instrument": the platform is subscribed to exactly
    the instruments requested via ``ensure_instrument_streaming`` (driven by
    the ``StreamCoordinator`` when a client joins an instrument room).  All
    bar storage and all message routing is strictly per-pair — a payload
    without a ``pair`` is logged and dropped, never attributed to a hidden
    default.

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
        history_hours: int = DEFAULT_HISTORY_HOURS,
        notifier: Notifier | None = None,
        market_filter: IMarketClosureFilter | None = None,
    ):
        """
        Initialize the ZMQ data source.

        Args:
            logger: Logger instance (required)
            gateway: Existing TradingGateway instance (or None to create one)
            gateway_config: Configuration for creating a new gateway
            history_hours: Number of hours of historical bars to load on connect
            market_filter: Optional filter that classifies gaps as scheduled market closures
        """
        self.logger = logger
        self.history_hours = history_hours
        self._gateway = gateway
        self._gateway_config = gateway_config or GatewayConfig()
        self._owns_gateway = gateway is None
        self._market_filter = market_filter
        self._coordinator = None

        # Track the first connect so we always refresh history on startup.
        # After that, only refresh if the disconnect lasted long enough to be "real".
        self._first_platform_connect: bool = True

        # Bar storage, strictly per pair.  Each pair has its own lock so
        # ingestion for one instrument never blocks another.
        self._bars_by_pair: dict[str, list[dict]] = {}
        self._bars_locks: dict[str, threading.RLock] = defaultdict(threading.RLock)

        # Instruments the user selected (multi-instrument mode), keyed by
        # full_name.  Re-subscribed on every platform reconnect.  Recorded
        # even while DISCONNECTED so instruments requested before the stream
        # starts are subscribed on the first platform connect.
        self._requested_instruments: dict[str, Instrument] = {}
        self._requested_instruments_lock = threading.Lock()
        self._state = DataSourceState.DISCONNECTED

        # Pairs with a refresh in flight (REFRESH_START seen, HISTORY_END
        # pending).  The connection leaves REFRESHING only when every
        # requested pair has completed its history.
        self._refreshing_pairs: set[str] = set()

        # Buffer for live bars received during refresh, keyed by pair: one
        # instrument's refresh must not suppress or divert another's bars.
        self._refresh_buffer: dict[str, list[dict]] = {}
        self._refresh_buffer_lock = threading.RLock()

        # Callbacks (set by app_factory / readiness monitor)
        self.on_history_complete: Callable[[list[dict]], None] | None = None
        self.on_live_bar: Callable[[dict], None] | None = None
        self.on_before_refresh: Callable[[], None] | None = None
        self.on_refresh_start: Callable[[], None] | None = None
        self.on_gap_detected: Callable[[int, str], None] | None = None
        self.on_heartbeat_stale: Callable[[float], None] | None = None
        self.on_late_history_batch: Callable[[int], None] | None = None

        # Readiness monitor injected by app_factory after construction
        self._readiness_monitor: ReadinessMonitor | None = None

        # CombinedDataSource interface
        self._stop_event = threading.Event()
        self._callback: Callable[[dict], None] | None = None
        self._from_time: int = 0
        self._cb_lock = threading.Lock()

        # Live bar building from ticks (for partial bars), one forming bar per pair.
        self._current_bars: dict[str, dict] = {}
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

        # Gap detection threshold (seconds). For 1m bars, anything > 1 min is a hole.
        self._gap_threshold: int = 60

        # Delay before first history request to let NinjaTrader populate its cache
        self._history_request_delay_sec: float = 1.0
        self._pending_refresh_timer: threading.Timer | None = None

        # Retry while CONNECTED: the platform can miss the first refresh request
        # (e.g. its command socket has not re-established after a rebind), and
        # CONNECTED otherwise has no way to recover — the readiness retry loop
        # only arms once a history_end arrives.
        self._history_retry_base_delay_sec: float = 5.0
        self._history_retry_max_delay_sec: float = 30.0
        self._history_retry_timer: threading.Timer | None = None
        self._history_retry_attempt: int = 0

        # Grace period for the disconnect notification to flush before the
        # gateway sockets close on stop().
        self._disconnect_flush_sec: float = 0.25

        # Notifier for alerts when bar stream dies
        self._notifier = notifier or NoOpNotifier()

        # Market status: suppress duplicate warnings when market is closed
        self._market_is_open: bool = True

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
            )
            self._owns_gateway = True
        return self._gateway

    # -------------------------------------------------------------------------
    # CombinedDataSource Interface
    # -------------------------------------------------------------------------

    def set_coordinator(self, coordinator) -> None:
        """Wire the data source to a ``StreamCoordinator`` for multi-pair routing."""
        self._coordinator = coordinator

    def set_readiness_monitor(self, monitor: ReadinessMonitor | None) -> None:
        """Inject the readiness monitor stopped together with this data source."""
        self._readiness_monitor = monitor

    def load_historical_bars(
        self,
        timeframe: str = "1m",
        start_time: int = None,
        end_time: int = None,
        *,
        pair: str,
    ) -> list[dict]:
        """
        Load historical bars for ``pair`` (synchronous - returns current cache).

        ``pair`` is required: there is no default instrument cache.  In live
        mode, historical bars are pushed from the platform via the
        HISTORY_BATCH message.
        """
        if not pair:
            raise ValueError("pair is required to load historical bars")

        with self._bars_locks[pair]:
            bars = list(self._bars_by_pair.get(pair, []))

        if start_time is not None:
            bars = [b for b in bars if b["time"] >= start_time]
        if end_time is not None:
            bars = [b for b in bars if b["time"] <= end_time]

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
        gateway.on(MessageType.MARKET_STATUS, self._on_market_status)

        # Register connection-state callback so ZMQDataSource manages its own lifecycle
        gateway.on_connection_change(self._on_gateway_connection_change)

        # Start the gateway
        gateway.start()

        # Start heartbeat monitor
        self._start_heartbeat_monitor()

        self.logger.info("ZMQDataSource started")

    def stop(self) -> None:
        """Stop receiving data.

        Always stops the gateway, even when it was injected: ``start()``
        starts the (possibly injected) gateway unconditionally, so ``stop()``
        must be symmetric.  Otherwise ``POST /api/stream/stop`` leaves the
        gateway running and a later start short-circuits to
        "already_connected" without ever re-binding.

        A disconnect notification is sent first so the platform can tell an
        intentional stop from a dead channel and stand down quietly.
        """
        self._cancel_pending_refresh_timer()
        self._cancel_history_retry_timer()
        monitor = self._readiness_monitor
        if monitor is not None:
            with contextlib.suppress(Exception):
                monitor.stop()
        self._stop_heartbeat_monitor()
        if self._gateway:
            # Notify the platform, then give the async command sender a brief
            # moment to flush it before the sockets close.
            with contextlib.suppress(Exception):
                self._gateway.send_disconnect("stream stopped")
            if self._gateway.is_running:
                time.sleep(self._disconnect_flush_sec)
            self._gateway.stop()
        self._state = DataSourceState.DISCONNECTED
        self._refreshing_pairs.clear()
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

    def _requested_instrument_names(self) -> str:
        """Comma-separated names of the currently requested instruments."""
        with self._requested_instruments_lock:
            names = sorted(self._requested_instruments)
        return ", ".join(names) if names else "no instruments selected"

    def _heartbeat_loop(self) -> None:
        """Background thread: alert if no completed bar received for too long."""
        while not self._heartbeat_stop_event.is_set():
            self._heartbeat_stop_event.wait(self._heartbeat_check_interval_sec)
            if self._heartbeat_stop_event.is_set():
                break

            # Only alert when we are actively streaming live bar messages
            if self._state != DataSourceState.STREAMING:
                continue

            elapsed = time.monotonic() - self._last_completed_bar_time
            if elapsed >= self._heartbeat_alert_threshold_sec:
                if not self._market_is_open:
                    # Market is reported closed; a stale bar stream is expected.
                    if not self._heartbeat_alert_sent:
                        self.logger.info(
                            f"[Heartbeat] No completed bar for {int(elapsed)}s but "
                            f"market is closed — suppressing alert"
                        )
                        self._heartbeat_alert_sent = True
                elif not self._heartbeat_alert_sent:
                    self._heartbeat_alert_sent = True
                    msg = (
                        f"🚨 ALERT: No completed bar received for "
                        f"{self._requested_instrument_names()} "
                        f"in {int(elapsed)}s. Strategy is blind. "
                        f"Check NinjaTrader ZMQ connector."
                    )
                    self.logger.error(f"[Heartbeat] {msg}")
                    try:
                        self._notifier.send(msg)
                    except Exception as e:
                        self.logger.error(f"[Heartbeat] FAILED to send notification: {type(e).__name__}: {e}")
                    if self.on_heartbeat_stale:
                        try:
                            self.on_heartbeat_stale(elapsed)
                        except Exception as e:
                            self.logger.error(f"[Heartbeat] on_heartbeat_stale error: {e}")
                    elif self._coordinator is not None:
                        self._coordinator.route_heartbeat_stale(elapsed)
            else:
                # Reset alert flag once bars resume
                if self._heartbeat_alert_sent:
                    self._heartbeat_alert_sent = False
                    self.logger.info(
                        f"[Heartbeat] Completed-bar stream resumed for "
                        f"{self._requested_instrument_names()}"
                    )

            # Stale-bar fallback: if no completed bar for >5min, treat market as closed
            # to suppress duplicate warnings from the forming bar being resent.
            if elapsed >= 300 and self._market_is_open:
                self._market_is_open = False
                self.logger.info(f"[MarketStatus] No completed bar for {int(elapsed)}s — treating market as CLOSED (stale-bar fallback)")

    def _payload_pair(self, payload: dict | None, kind: str) -> str | None:
        """Extract the mandatory ``pair`` from a message payload.

        Returns ``None`` (after logging a warning) when the payload carries
        no pair — there is no default instrument to fall back to.
        """
        pair = payload.get("pair") if payload else None
        if not pair:
            self.logger.warning(f"{kind} message without pair — dropped")
            return None
        return pair

    def _on_tick(self, payload: dict) -> None:
        """Handle incoming tick."""
        self._stats["ticks_received"] += 1

        tick_pair = self._payload_pair(payload, "TICK")
        if tick_pair is None:
            return

        # Build partial bar from tick
        tick_time = int(payload["time"])
        price = float(payload["price"])
        volume = int(payload.get("volume", 0))

        bar_time = (tick_time // 60) * 60

        current_bar = self._current_bars.get(tick_pair)

        # Reset on minute boundary
        if current_bar is not None and current_bar["time"] != bar_time:
            current_bar = None

        if current_bar is None:
            current_bar = {
                "time": bar_time,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "pair": tick_pair,
            }
            self._current_bars[tick_pair] = current_bar
        else:
            current_bar["high"] = max(current_bar["high"], price)
            current_bar["low"] = min(current_bar["low"], price)
            current_bar["close"] = price
            current_bar["volume"] += volume

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
            if self._state == DataSourceState.CONNECTED or tick_pair in self._refreshing_pairs:
                return  # Suppress partial bars before this pair's history is ready
            partial = dict(current_bar)
            partial["partial"] = True
            if self._coordinator is not None:
                self._coordinator.route_partial_bar(partial)
            elif self.on_live_bar:
                self.on_live_bar(partial)

    def _on_bar(self, payload: dict) -> None:
        """Handle completed bar from platform."""
        self._stats["bars_received"] += 1

        bar_pair = self._payload_pair(payload, "BAR")
        if bar_pair is None:
            return

        # DEBUG: Log every bar for the first 100 streaming bars, then every 50th
        is_streaming = self._state == DataSourceState.STREAMING
        if is_streaming and (
            self._stats["bars_received"] <= 100 or self._stats["bars_received"] % 50 == 0
        ):
            self.logger.info(
                    f"[LIVE BAR #{self._stats['bars_received']}] "
                    f"time={payload.get('time')} close={payload.get('close')} pair={bar_pair}"
                )

        if is_streaming:
            self._last_completed_bar_time = time.monotonic()
            # Reset alert flag when a bar arrives
            if self._heartbeat_alert_sent:
                self._heartbeat_alert_sent = False
                self.logger.info(f"[Heartbeat] Completed-bar stream resumed for {bar_pair}")
            # Reset market-open flag when a bar arrives (stale-bar fallback recovery)
            if not self._market_is_open:
                self._market_is_open = True
                self.logger.info("[MarketStatus] Bar received while flagged closed — treating market as OPEN")

        bar = {
            "time": int(payload["time"]),
            "open": float(payload["open"]),
            "high": float(payload["high"]),
            "low": float(payload["low"]),
            "close": float(payload["close"]),
            "volume": int(payload.get("volume", 0)),
            "pair": bar_pair,
        }

        # Buffer live bars only while THIS pair's history is not ready: the
        # CONNECTED pre-history phase (any pair) or this pair's own refresh.
        # Other instruments keep streaming undisturbed while one refreshes.
        if self._state == DataSourceState.CONNECTED or bar_pair in self._refreshing_pairs:
            with self._refresh_buffer_lock:
                self._refresh_buffer.setdefault(bar_pair, []).append(bar)
            if self._coordinator is not None:
                self._coordinator.route_bar(bar)
            return  # Buffer live bars before this pair's history is ready

        inserted_idx = self._store_bar(bar, bar_pair)

        # Gap detection: check neighbors of the inserted bar
        if inserted_idx >= 0:
            context = "STREAMING" if self._state == DataSourceState.STREAMING else "INGEST"
            with self._bars_locks[bar_pair]:
                bars = self._bars_by_pair[bar_pair]
                prev_time = bars[inserted_idx - 1]["time"] if inserted_idx > 0 else None
                next_time = (
                    bars[inserted_idx + 1]["time"] if inserted_idx < len(bars) - 1 else None
                )
            for before, after in ((prev_time, bar["time"]), (bar["time"], next_time)):
                if before is None or after is None:
                    continue
                gap = self._detect_gap(before, after, context)
                if gap and self._state == DataSourceState.STREAMING:
                    if self.on_gap_detected:
                        self.on_gap_detected(gap, context)
                    elif self._coordinator is not None:
                        self._coordinator.route_gap_detected(gap, context, bar_pair)

        if self.on_live_bar:
            self.on_live_bar(bar)

        if self._coordinator is not None:
            self._coordinator.route_bar(bar)

    def _store_bar(self, bar: dict, pair: str) -> int:
        """Insert a completed bar into the pair's cache.

        Returns the insertion index, or -1 when the bar was an exact
        duplicate (no state changed).
        """
        lock = self._bars_locks[pair]
        with lock:
            bars = self._bars_by_pair.setdefault(pair, [])
            if not bars or bar["time"] > bars[-1]["time"]:
                bars.append(bar)
                return len(bars) - 1

            # Out of order - insert correctly
            times = [b["time"] for b in bars]
            idx = bisect.bisect_left(times, bar["time"])
            if idx < len(times) and times[idx] == bar["time"]:
                existing = bars[idx]
                updated = False
                for f in ("open", "high", "low", "close", "volume"):
                    if existing.get(f) != bar.get(f):
                        existing[f] = bar[f]
                        updated = True
                if updated:
                    self.logger.info(
                        f"[LiveUpdate] Bar t={bar['time']} updated from live stream"
                    )
                    # Mark as inserted so gap detection still runs
                    return idx
                self._duplicate_count += 1
                if self._market_is_open:
                    self.logger.warning(
                        f"Duplicate bar at time {bar['time']} (total={self._duplicate_count})"
                    )
                return -1
            bars.insert(idx, bar)
            self.logger.warning(f"Bar out of order: inserted at index {idx}")
            return idx

    def _on_partial_bar(self, payload: dict) -> None:
        """Handle partial bar from platform."""
        pair = self._payload_pair(payload, "PARTIAL_BAR")
        if pair is None:
            return
        if self._state == DataSourceState.CONNECTED or pair in self._refreshing_pairs:
            return  # Suppress partial bars before this pair's history is ready
        self._last_native_partial_time = time.monotonic()
        partial = dict(payload)
        partial["partial"] = True
        if self._coordinator is not None:
            self._coordinator.route_partial_bar(partial)
        elif self.on_live_bar:
            self.on_live_bar(partial)

    def _on_history_batch(self, payload: dict) -> None:
        """Handle batch of historical bars."""
        self._stats["history_batches"] += 1

        pair = self._payload_pair(payload, "HISTORY_BATCH")
        if pair is None:
            return

        bars = payload.get("bars", [])
        payload.get("days", 1)

        new_bars = []
        for raw in bars:
            bar = {
                "time": int(raw["time"]),
                "open": float(raw["open"]),
                "high": float(raw["high"]),
                "low": float(raw["low"]),
                "close": float(raw["close"]),
                "volume": int(raw.get("volume", 0)),
                "pair": raw.get("pair", pair),
            }
            new_bars.append(bar)

        lock = self._bars_locks[pair]
        with lock:
            cache = self._bars_by_pair.setdefault(pair, [])
            existing_times = {b["time"] for b in cache}
            added = 0
            for bar in new_bars:
                if bar["time"] not in existing_times:
                    cache.append(bar)
                    existing_times.add(bar["time"])
                    added += 1
            if added > 0 and len(cache) > 1:
                cache.sort(key=lambda b: b["time"])
            total_cached = len(cache)

        self.logger.info(
            f"RECV: history_batch | pair={pair} | bars={len(new_bars)} | "
            f"unique_added={added} | total_cached={total_cached}"
        )

        # Notify readiness monitor if gap-fill arrives after we're already streaming
        if self._state == DataSourceState.STREAMING and added > 0:
            if self.on_late_history_batch:
                try:
                    self.on_late_history_batch(added)
                except Exception as e:
                    self.logger.error(f"Error in on_late_history_batch: {e}")
            elif self._coordinator is not None:
                self._coordinator.route_late_history_batch(added, pair)

    def _detect_gap(self, prev_time: int, curr_time: int, context: str) -> int:
        """Log a warning if there is a gap between two bar timestamps.

        Returns the gap size in seconds, or 0 if no gap was detected.
        """
        gap = curr_time - prev_time
        if gap > self._gap_threshold:
            self._gap_count += 1
            if not self._market_is_open:
                # Market is closed — gap is expected (lunch break, overnight, etc.)
                return gap
            chicago = ZoneInfo("America/Chicago")
            dt_prev = datetime.fromtimestamp(prev_time, tz=timezone.utc).astimezone(chicago).strftime('%Y-%m-%d %H:%M:%S %Z')
            dt_curr = datetime.fromtimestamp(curr_time, tz=timezone.utc).astimezone(chicago).strftime('%Y-%m-%d %H:%M:%S %Z')
            self.logger.warning(
                f"🕳️  GAP DETECTED [{context}]: {gap}s hole between {dt_prev} and {dt_curr} "
                f"({gap // 60}m {gap % 60}s)"
            )
            return gap
        return 0

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

    def _on_history_end(self, payload: dict | None = None) -> None:
        """Handle end of historical data for one pair."""
        # History is complete; the pending delayed refresh is no longer needed.
        self._cancel_pending_refresh_timer()

        pair = self._payload_pair(payload, "HISTORY_END")
        if pair is None:
            return

        with self._bars_locks[pair]:
            bars_copy = list(self._bars_by_pair.get(pair, []))

        # Reset gap count so old gaps don't accumulate forever
        self._gap_count = 0
        # Scan loaded history for gaps so we know if the source already had holes
        gap_count = self._scan_for_gaps(bars_copy, "HISTORY")

        # The data source no longer decides readiness here.  Completeness/freshness
        # checks are performed by the ReadinessMonitor using the helper below.

        # NinjaTrader's BarsRequest can return partial cached data on a cold start.
        # The data provider backfills asynchronously; gap-fill is already running
        # on the NT side (SendGapFillAsync). Hammering NT with rapid retries just
        # exhausts the retry budget before gap-fill can arrive. Switch to STREAMING
        # so live bars flow, and let gap-fill batches arrive naturally.
        if bars_copy:
            last_bar_time = bars_copy[-1]["time"]
            now = time.time()
            gap_to_now = now - last_bar_time
            if gap_to_now > 600:  # > 10 minutes stale
                dt_str = datetime.fromtimestamp(last_bar_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                self.logger.warning(
                    f"🕳️  History ends {gap_to_now:.0f}s ago ({dt_str} UTC) — "
                    f"NT cache is still warming. Gap-fill is in progress on NT side. "
                    f"Switching to STREAMING and waiting for gap-fill batches..."
                )

        # This pair's refresh is done.  The connection switches to STREAMING
        # once every requested pair has completed its history.
        self._refreshing_pairs.discard(pair)
        refresh_done = not self._refreshing_pairs
        if refresh_done:
            self._state = DataSourceState.STREAMING
            # Reset heartbeat baseline so the first streaming bar has a full grace period
            self._last_completed_bar_time = time.monotonic()

        self.logger.info(
            f"History complete for {pair}: {len(bars_copy)} bars cached"
            + (", switching to STREAMING mode" if refresh_done else "")
        )
        if gap_count > 0:
            self.logger.warning(f"🕳️  HISTORY SCAN: {gap_count} total gap(s) detected in {len(bars_copy)} bars")

        if self.on_history_complete:
            try:
                self.on_history_complete(bars_copy)
            except Exception as e:
                self.logger.error(f"Error in on_history_complete: {e}")

        # Flush live bars buffered for THIS pair during its refresh; buffers
        # of other pairs (still refreshing) are left untouched.
        with self._refresh_buffer_lock:
            buffered = self._refresh_buffer.pop(pair, [])
        if buffered:
            self.logger.info(f"Flushing {len(buffered)} live bars buffered during refresh for {pair}")
            for buffered_bar in buffered:
                self._on_bar(buffered_bar)

        if self._coordinator is not None:
            self._coordinator.route_history_loaded(bars_copy, pair=pair)

    def _on_refresh_start(self, payload: dict | None = None) -> None:
        """Handle refresh start for one pair - clear its recent data."""
        pair = self._payload_pair(payload, "REFRESH_START")
        if pair is None:
            return

        if pair in self._refreshing_pairs:
            self.logger.info(f"Refresh start for {pair} ignored: already refreshing")
            return

        # The platform actually started a refresh — the CONNECTED history
        # retry has done its job.
        self._cancel_history_retry_timer()

        self.logger.info(f"Refresh start for {pair} - clearing recent data")

        if self._coordinator is not None:
            self._coordinator.route_before_refresh(pair)
            self._coordinator.route_refresh_start(pair)

        if self.on_before_refresh:
            try:
                self.on_before_refresh()
            except Exception as e:
                self.logger.error(f"Error in on_before_refresh: {e}")

        # Keep bars older than 1 day
        cutoff = int(time.time()) - 86400
        with self._bars_locks[pair]:
            existing = self._bars_by_pair.get(pair, [])
            preserved = [b for b in existing if b["time"] < cutoff]
            removed = len(existing) - len(preserved)
            self._bars_by_pair[pair] = preserved

        self._current_bars.pop(pair, None)

        previous_state = self._state
        self._refreshing_pairs.add(pair)
        self._state = DataSourceState.REFRESHING
        if previous_state != DataSourceState.CONNECTED:
            # Stale buffered bars from an earlier incomplete cycle of THIS
            # pair only — other pairs' buffers must survive.
            with self._refresh_buffer_lock:
                self._refresh_buffer.pop(pair, None)

        if self.on_refresh_start:
            try:
                self.on_refresh_start()
            except Exception as e:
                self.logger.error(f"Error in on_refresh_start: {e}")

        self.logger.info(f"Refresh start for {pair}: preserved {len(preserved)} historical bars, removed {removed} recent bars")

    def _on_market_status(self, payload: dict) -> None:
        """Handle market status notification from platform."""
        market_open = payload.get("market_open", True)
        next_open = payload.get("next_open", 0)
        pair = payload.get("pair", "unknown")

        if market_open != self._market_is_open:
            self._market_is_open = market_open
            status = "OPEN" if self._market_is_open else "CLOSED"
            self.logger.info(f"[MarketStatus] {pair} market is now {status}, next_open={next_open}")
        else:
            # Log at debug level when status hasn't changed
            self.logger.debug(f"[MarketStatus] {pair} still {'OPEN' if self._market_is_open else 'CLOSED'}")

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def ensure_instrument_streaming(self, instrument: Instrument) -> None:
        """Subscribe an instrument and request its history on demand.

        Used by the ``StreamCoordinator`` when a client joins an instrument
        room.  Idempotent: a second call for the same instrument is a no-op.
        Requested instruments are re-subscribed automatically on every
        platform reconnect (see ``on_platform_connected``).

        If the platform is not connected yet, the instrument is still recorded
        so the next ``on_platform_connected`` subscribes it — clients typically
        join their instrument room on page load, before streaming starts.
        """
        full_name = getattr(instrument, "full_name", None)
        if not full_name:
            self.logger.debug(
                f"ensure_instrument_streaming skipped for {getattr(instrument, 'symbol', '?')}: no full_name"
            )
            return
        with self._requested_instruments_lock:
            if full_name in self._requested_instruments:
                self.logger.debug(
                    f"ensure_instrument_streaming skipped for {full_name}: already requested"
                )
                return
            self._requested_instruments[full_name] = instrument
        if self._state == DataSourceState.DISCONNECTED:
            self.logger.info(
                f"Queued instrument {full_name} ({instrument.symbol}) — will subscribe on platform connect"
            )
            return
        self._subscribe_and_refresh(instrument)

    def _subscribe_and_refresh(self, instrument: Instrument) -> None:
        """Send subscribe + history refresh for an instrument to the platform."""
        gateway = self._ensure_gateway()
        days = max(1, math.ceil(self.history_hours / 24))
        gateway.send_subscribe(instrument.full_name)
        self.logger.info(f"Subscribed to instrument {instrument.full_name} ({instrument.symbol})")
        gateway.send_refresh_request(days=days, instrument=instrument.full_name)
        self.logger.info(f"Requested {days}d history refresh for {instrument.full_name}")

    def _resolve_refresh_targets(self, pair: str | None) -> list[str]:
        """Resolve which instruments a refresh request should target.

        ``pair`` may be a full name (``"MNQ 09-26"``) or a symbol
        (``"MNQ"``); when omitted, every requested instrument is refreshed.
        """
        with self._requested_instruments_lock:
            requested = dict(self._requested_instruments)
        if pair is None:
            return list(requested)
        if pair in requested:
            return [pair]
        for full_name, instrument in requested.items():
            if instrument.symbol == pair:
                return [full_name]
        # Explicitly requested by the caller — send it as given.
        self.logger.warning(f"request_refresh: {pair} is not a requested instrument")
        return [pair]

    def request_refresh(self, days: int = None, pair: str | None = None) -> None:
        """Request historical data refresh from the platform.

        Refreshes every requested instrument, or just ``pair`` when given.
        The public setting is in hours, but the NinjaTrader connector expects
        whole days, so we convert hours to days (ceil) when no explicit day
        count is provided.
        """
        if self._state == DataSourceState.REFRESHING:
            self.logger.info("Refresh request ignored: already refreshing")
            return
        if self._state == DataSourceState.DISCONNECTED:
            self.logger.info("Refresh request ignored: platform not connected")
            return
        gateway = self._ensure_gateway()
        if days is None:
            days = max(1, math.ceil(self.history_hours / 24))
        targets = self._resolve_refresh_targets(pair)
        if not targets:
            self.logger.info("Refresh request ignored: no instruments requested yet")
            return
        for full_name in targets:
            gateway.send_refresh_request(days=days, instrument=full_name)

    @property
    def is_streaming(self) -> bool:
        """Check if the data source is consuming live market messages."""
        return self._state == DataSourceState.STREAMING

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

    def _arm_history_retry(self) -> None:
        """Schedule a subscribe+refresh retry while stuck in CONNECTED."""
        self._cancel_history_retry_timer()
        delay = min(
            self._history_retry_base_delay_sec * (2 ** self._history_retry_attempt),
            self._history_retry_max_delay_sec,
        )
        self._history_retry_timer = threading.Timer(delay, self._on_history_retry)
        self._history_retry_timer.daemon = True
        self._history_retry_timer.start()

    def _cancel_history_retry_timer(self) -> None:
        """Cancel any pending history retry."""
        if self._history_retry_timer is not None:
            self._history_retry_timer.cancel()
            self._history_retry_timer = None

    def _on_history_retry(self) -> None:
        """Re-send subscribe+refresh when the platform never answered."""
        self._history_retry_timer = None
        if self._state != DataSourceState.CONNECTED:
            return
        with self._requested_instruments_lock:
            instruments = list(self._requested_instruments.values())
        if not instruments:
            # Nothing requested — nothing to retry.
            return
        self._history_retry_attempt += 1
        attempt = self._history_retry_attempt
        self.logger.warning(
            f"No history received after connect — retrying subscribe+refresh "
            f"(attempt {attempt})"
        )
        if attempt >= 3 and attempt % 6 == 3:
            # The platform is connected (heartbeats flow) but never answers
            # commands — its command channel is most likely wedged. Retries
            # may still heal it; make sure the user knows where to look.
            try:
                self._notifier.send(
                    f"⚠️ NinjaTrader connector is not responding to commands "
                    f"(attempt {attempt}). Check NinjaTrader / restart the connector."
                )
            except Exception as e:
                self.logger.error(f"Failed to send notifier alert: {e}")
        gateway = self._ensure_gateway()
        for instrument in instruments:
            try:
                gateway.send_subscribe(instrument.full_name)
            except Exception as e:
                self.logger.error(f"Failed to re-subscribe to {instrument.full_name}: {e}")
        self.request_refresh()
        self._arm_history_retry()

    def _do_delayed_refresh(self) -> None:
        """Execute the delayed refresh request."""
        self._pending_refresh_timer = None
        if self._state == DataSourceState.DISCONNECTED:
            self.logger.info("Delayed refresh aborted: platform disconnected")
            return
        # If history arrived before the delay expired we are already past the
        # CONNECTED state; requesting another refresh would be redundant.
        if self._state != DataSourceState.CONNECTED:
            self.logger.info(
                f"Delayed refresh aborted: already in {self._state.name}"
            )
            return
        self.logger.info("Requesting historical data refresh after delay")
        self._history_retry_attempt = 0
        self.request_refresh()
        self._arm_history_retry()

    def _cached_bars_for(self, instrument: Instrument) -> list[dict]:
        """Snapshot of the cached bars for an instrument (by symbol)."""
        with self._bars_locks[instrument.symbol]:
            return list(self._bars_by_pair.get(instrument.symbol, []))

    def on_platform_connected(self) -> None:
        """Called when the platform connects. Auto-request refresh if needed."""
        if self._state != DataSourceState.DISCONNECTED:
            self.logger.debug(f"Platform connected ignored: state={self._state.name}")
            return

        self._state = DataSourceState.CONNECTED

        gateway = self._ensure_gateway()

        with self._requested_instruments_lock:
            requested = list(self._requested_instruments.values())

        if not requested:
            # No instrument selected yet — stay CONNECTED.  Instruments
            # requested later (ensure_instrument_streaming) are subscribed
            # immediately while connected.
            self.logger.info(
                "Platform connected — waiting for instrument selection; "
                "nothing to subscribe yet"
            )
            return

        should_refresh = (
            self._first_platform_connect
            or self._gateway is None
            or self._gateway.was_last_disconnect_real
        )
        self._first_platform_connect = False

        if not should_refresh:
            # Brief reconnect. If every requested instrument's cached history
            # is still fresh/complete we can resume streaming immediately;
            # otherwise treat it as a real reconnect.
            checks = [
                (instrument, *self.check_history_completeness(self._cached_bars_for(instrument)))
                for instrument in requested
            ]
            if all(ok for _, ok, _ in checks):
                self.logger.info(
                    "Platform reconnected after brief blip — cached history is fresh; resuming streaming"
                )
                self._state = DataSourceState.STREAMING
                self._last_completed_bar_time = time.monotonic()
                for instrument in requested:
                    bars_copy = self._cached_bars_for(instrument)
                    if self.on_history_complete:
                        try:
                            self.on_history_complete(bars_copy)
                        except Exception as e:
                            self.logger.error(f"Error re-notifying history complete: {e}")
                    elif self._coordinator is not None:
                        self._coordinator.route_history_loaded(bars_copy, pair=instrument.symbol)
                return
            else:
                reason = next(r for _, ok, r in checks if not ok)
                self.logger.info(
                    f"Platform reconnected after brief blip — cached history stale ({reason}); treating as real reconnect"
                )
                should_refresh = True

        # Tell NinjaTrader which instruments to stream.  Platform-side
        # subscriptions do not survive a reconnect, so every requested
        # instrument is (re-)subscribed here; history refresh follows after a
        # short delay (see _do_delayed_refresh) to let the platform populate
        # its cache.
        for instrument in requested:
            try:
                gateway.send_subscribe(instrument.full_name)
            except Exception as e:
                self.logger.error(f"Failed to subscribe to {instrument.full_name}: {e}")

        delay = self._history_request_delay_sec
        names = ", ".join(i.full_name for i in requested)
        self.logger.info(f"Platform connected, instruments=[{names}], requesting historical data refresh in {delay}s")
        self._cancel_pending_refresh_timer()
        self._pending_refresh_timer = threading.Timer(delay, self._do_delayed_refresh)
        self._pending_refresh_timer.start()

    def on_platform_disconnected(self) -> None:
        """Called when the platform disconnects."""
        if self._state != DataSourceState.DISCONNECTED:
            self._state = DataSourceState.DISCONNECTED
            self._refreshing_pairs.clear()
            self._cancel_pending_refresh_timer()
            self._cancel_history_retry_timer()
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

    def check_history_completeness(self, bars: list[dict] | None = None) -> tuple[bool, str]:
        """
        Pure helper: check if the supplied bars are fresh and gap-free enough
        for indicator calculations.  Does NOT mutate readiness state.

        Callers pass the bars of their own pair — there is no default cache
        to fall back to, so ``None`` simply means "no data".
        """
        if not bars:
            return False, "No historical data received"

        now = int(time.time())
        last_bar_time = bars[-1]["time"]
        age_sec = now - last_bar_time

        if age_sec > 60:
            return False, f"Last bar is {age_sec // 60}m old (need < 1m)"

        # Check for gaps in the last 2 hours, ignoring scheduled market closures.
        cutoff = now - 7200
        last_checked = None
        for bar in bars:
            if bar["time"] < cutoff:
                continue
            if last_checked is not None:
                gap = bar["time"] - last_checked
                if gap > self._gap_threshold:
                    if self._market_filter is not None and self._market_filter.is_market_closed_gap(last_checked, bar["time"]):
                        continue
                    return False, f"Gap detected: {gap // 60}m hole in last 2h"
            last_checked = bar["time"]

        return True, "OK"

    def get_health(self) -> dict:
        """Get current stream health snapshot (data-source metrics only)."""
        per_pair = {}
        for pair in list(self._bars_by_pair):
            with self._bars_locks[pair]:
                bars = self._bars_by_pair.get(pair, [])
                per_pair[pair] = {
                    "bars_cached": len(bars),
                    "last_bar_time": bars[-1]["time"] if bars else None,
                }

        heartbeat_age = time.monotonic() - self._last_completed_bar_time if self._last_completed_bar_time > 0 else None

        with self._requested_instruments_lock:
            instruments = sorted(self._requested_instruments)

        return {
            "state": self._state.name,
            "pairs": per_pair,
            "instruments": instruments,
            "heartbeat_age_sec": round(heartbeat_age, 1) if heartbeat_age is not None else None,
            "duplicate_count": self._duplicate_count,
            "gap_count": self._gap_count,
            "ticks_received": self._stats["ticks_received"],
            "bars_received": self._stats["bars_received"],
            "history_batches": self._stats["history_batches"],
            "platform_connected": self.is_connected,
        }

    @property
    def stats(self) -> dict[str, int]:
        """Get reception statistics."""
        return dict(self._stats)
