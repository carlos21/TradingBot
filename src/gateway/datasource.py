"""
ZeroMQ Data Source - Receives market data from trading platforms.

This module provides a CombinedDataSource implementation that receives
bars and ticks from trading platforms via ZeroMQ.
"""

from __future__ import annotations
import threading
import time
from typing import Callable, Dict, List, Optional
from collections import defaultdict
from datetime import datetime, timezone
import bisect
import logging

from src.data_sources.combined_datasource import CombinedDataSource
from src.utils.app_logger import ILogger, ConsoleLogger
from .gateway import TradingGateway, GatewayConfig
from .protocol import MessageType


logger = logging.getLogger(__name__)


class ZMQDataSource(CombinedDataSource):
    """
    Data source that receives market data from trading platforms via ZeroMQ.
    
    This provides a fast, reliable ZeroMQ-based data source for live trading.
    more efficient ZeroMQ implementation.
    
    Usage:
        from src.gateway import TradingGateway, ZMQDataSource
        
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
        gateway: Optional[TradingGateway] = None,
        gateway_config: Optional[GatewayConfig] = None,
        pair: str = "MNQ",
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
        self._gateway = gateway
        self._gateway_config = gateway_config or GatewayConfig()
        self._owns_gateway = gateway is None
        
        # Data storage
        self._historical_bars: List[Dict] = []
        self._bars_lock = threading.RLock()
        self._live = False
        self._refreshing = False
        self._last_history_time: int = 0
        
        # Buffer for live bars received during refresh
        self._refresh_buffer: List[Dict] = []
        
        # Callbacks (set by app_factory)
        self.on_history_complete: Optional[Callable[[List[Dict]], None]] = None
        self.on_live_bar: Optional[Callable[[Dict], None]] = None
        self.on_before_refresh: Optional[Callable[[], None]] = None
        
        # CombinedDataSource interface
        self._stop_event = threading.Event()
        self._callback: Optional[Callable[[Dict], None]] = None
        self._from_time: int = 0
        self._cb_lock = threading.Lock()
        
        # Live bar building from ticks (for partial bars)
        self._current_bar: Optional[Dict] = None
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
        
        # Gap detection threshold (seconds). For 1m bars, anything > 2 min is a hole.
        self._gap_threshold: int = 120
    
    def _ensure_gateway(self) -> TradingGateway:
        """Get or create the gateway."""
        if self._gateway is None:
            self._gateway = TradingGateway(
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
    ) -> List[Dict]:
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
            return [{k: v for k, v in b.items()} for b in unique_bars]
        
        # Aggregate into higher timeframes
        return self._aggregate_bars(unique_bars, timeframe)
    
    def _aggregate_bars(self, bars: List[Dict], timeframe: str) -> List[Dict]:
        """Aggregate 1m bars into higher timeframes."""
        unit = timeframe[-1]
        num = int(timeframe[:-1])
        
        if unit == "m":
            window_secs = num * 60
        elif unit == "h":
            window_secs = num * 3600
        else:
            return list(bars)
        
        buckets: Dict[int, List[Dict]] = defaultdict(list)
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
        callback: Callable[[Dict], None],
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
        
        # Register callbacks
        gateway.on(MessageType.TICK, self._on_tick)
        gateway.on(MessageType.BAR, self._on_bar)
        gateway.on(MessageType.PARTIAL_BAR, self._on_partial_bar)
        gateway.on(MessageType.HISTORY_BATCH, self._on_history_batch)
        gateway.on(MessageType.HISTORY_END, self._on_history_end)
        gateway.on(MessageType.REFRESH_START, self._on_refresh_start)
        
        # Start the gateway
        gateway.start()
        
        self.logger.info("ZMQDataSource started")
    
    def stop(self) -> None:
        """Stop receiving data."""
        if self._owns_gateway and self._gateway:
            self._gateway.stop()
        self.logger.info("ZMQDataSource stopped")
    
    def _on_tick(self, payload: Dict) -> None:
        """Handle incoming tick."""
        self._stats["ticks_received"] += 1
        
        # Log every 100th tick to avoid spam
        if self._stats["ticks_received"] % 100 == 0:
            self.logger.debug(f"Ticks received: {self._stats['ticks_received']} (latest: {payload.get('price')})")
        
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
    
    def _on_bar(self, payload: Dict) -> None:
        """Handle completed bar from platform."""
        self._stats["bars_received"] += 1
        
        bar = {
            "time": int(payload["time"]),
            "open": float(payload["open"]),
            "high": float(payload["high"]),
            "low": float(payload["low"]),
            "close": float(payload["close"]),
            "volume": int(payload.get("volume", 0)),
            "pair": payload.get("pair", self.pair),
        }
        
        if self._refreshing:
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
                    self.logger.warning(f"Duplicate bar at time {bar['time']}")
                    return
                self._historical_bars.insert(idx, bar)
                inserted_idx = idx
                self.logger.warning(f"Bar out of order: inserted at index {idx}")
            
            # Gap detection: check neighbors of the inserted bar
            if inserted_idx > 0:
                self._detect_gap(
                    self._historical_bars[inserted_idx - 1]["time"],
                    bar["time"],
                    "LIVE" if self._live else "INGEST"
                )
            if inserted_idx < len(self._historical_bars) - 1:
                self._detect_gap(
                    bar["time"],
                    self._historical_bars[inserted_idx + 1]["time"],
                    "LIVE" if self._live else "INGEST"
                )
        
        if self.on_live_bar:
            self.on_live_bar(bar)
    
    def _on_partial_bar(self, payload: Dict) -> None:
        """Handle partial bar from platform."""
        self._last_native_partial_time = time.monotonic()
        if self.on_live_bar:
            partial = dict(payload)
            partial["partial"] = True
            self.on_live_bar(partial)
    
    def _on_history_batch(self, payload: Dict) -> None:
        """Handle batch of historical bars."""
        self._stats["history_batches"] += 1
        
        bars = payload.get("bars", [])
        days = payload.get("days", 1)
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
            dt_prev = datetime.fromtimestamp(prev_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            dt_curr = datetime.fromtimestamp(curr_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            self.logger.warning(
                f"🕳️  GAP DETECTED [{context}]: {gap}s hole between {dt_prev} and {dt_curr} "
                f"({gap // 60}m {gap % 60}s)"
            )
    
    def _scan_for_gaps(self, bars: List[Dict], context: str) -> int:
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
    
    def _on_history_end(self, payload: Dict = None) -> None:
        """Handle end of historical data."""
        self._live = True
        self._refreshing = False  # Resume accepting live bars
        
        with self._bars_lock:
            if self._historical_bars:
                self._last_history_time = self._historical_bars[-1]["time"]
            bars_copy = list(self._historical_bars)
            
            # Scan loaded history for gaps so we know if the source already had holes
            gap_count = self._scan_for_gaps(self._historical_bars, "HISTORY")
        
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
    
    def _on_refresh_start(self, payload: Dict = None) -> None:
        """Handle refresh start - clear recent data."""
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
        
        self._live = False
        self._current_bar = None
        self._refreshing = True
        self._refresh_buffer.clear()
        
        self.logger.info(f"Refresh start: preserved {len(preserved)} historical bars, removed {removed} recent bars")
    
    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    
    def request_refresh(self, days: int = 1) -> None:
        """Request historical data refresh from platform."""
        gateway = self._ensure_gateway()
        gateway.send_refresh_request(days=days)
    
    @property
    def is_live(self) -> bool:
        """Check if receiving live data."""
        return self._live
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to platform."""
        return self._gateway is not None and self._gateway.is_connected
    
    @property
    def gateway(self) -> Optional[TradingGateway]:
        """Access the underlying TradingGateway for advanced configuration."""
        return self._gateway
    
    @property
    def stats(self) -> Dict[str, int]:
        """Get reception statistics."""
        return dict(self._stats)
