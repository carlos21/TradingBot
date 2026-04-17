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
        pair: str = "NQ",
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
        
        # Callbacks (set by app_factory)
        self.on_history_complete: Optional[Callable[[List[Dict]], None]] = None
        self.on_live_bar: Optional[Callable[[Dict], None]] = None
        self.on_before_refresh: Optional[Callable[[], None]] = None
        
        # CombinedDataSource interface
        self._stop_event = threading.Event()
        self._callback: Optional[Callable[[Dict], None]] = None
        self._from_time: int = 0
        self._cb_lock = threading.Lock()
        
        # Stats
        self._stats = {
            "ticks_received": 0,
            "bars_received": 0,
            "history_batches": 0,
        }
    
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
        
        if timeframe == "1m":
            return [{k: v for k, v in b.items()} for b in bars]
        
        # Aggregate into higher timeframes
        return self._aggregate_bars(bars, timeframe)
    
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
            return  # Drop live bars during refresh
        
        # Insert in sorted order (platform should send in order, but be safe)
        with self._bars_lock:
            if not self._historical_bars or bar["time"] > self._historical_bars[-1]["time"]:
                self._historical_bars.append(bar)
            else:
                # Out of order - insert correctly
                times = [b["time"] for b in self._historical_bars]
                idx = bisect.bisect_left(times, bar["time"])
                if idx < len(times) and times[idx] == bar["time"]:
                    self.logger.warning(f"Duplicate bar at time {bar['time']}")
                    return
                self._historical_bars.insert(idx, bar)
                self.logger.warning(f"Bar out of order: inserted at index {idx}")
        
        if self.on_live_bar:
            self.on_live_bar(bar)
    
    def _on_partial_bar(self, payload: Dict) -> None:
        """Handle partial bar from platform."""
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
            self._historical_bars.extend(new_bars)
        
        self.logger.info(f"RECV: history_batch | pair={pair} | bars={len(new_bars)} | days={days} | total_cached={len(self._historical_bars)}")
    
    def _on_history_end(self, payload: Dict = None) -> None:
        """Handle end of historical data."""
        self._live = True
        
        with self._bars_lock:
            if self._historical_bars:
                self._last_history_time = self._historical_bars[-1]["time"]
            bars_copy = list(self._historical_bars)
        
        self.logger.info(f"History complete: {len(bars_copy)} bars cached, switching to LIVE mode")
        
        if self.on_history_complete:
            try:
                self.on_history_complete(bars_copy)
            except Exception as e:
                self.logger.error(f"Error in on_history_complete: {e}")
    
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
        self._refreshing = True
        
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
