"""Application service for on-demand bar parity checks.

Orchestrates fetching local bars, requesting remote bars from NinjaTrader,
comparing them, and filtering market-closed gaps.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from src.domain.parity import IMarketClosureFilter, IParityChecker, ParityResult
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.gateway.gateway import TradingGateway
from src.utils.app_logger import ILogger


class ParityCheckService:
    """Service that performs a one-shot parity check between Python cache and NT.

    Thread-safe. Can be called from any thread (e.g. Socket.IO handler).
    """

    def __init__(
        self,
        data_source: ZMQDataSource,
        gateway: TradingGateway,
        checker: IParityChecker,
        market_filter: IMarketClosureFilter,
        logger: ILogger,
        pair: str | None = None,
        instrument: str | None = None,
    ):
        self._data_source = data_source
        self._gateway = gateway
        self._checker = checker
        self._market_filter = market_filter
        self.logger = logger
        # Instrument under check: scopes the local bar cache read and the
        # audit request.  Required — there is no default instrument.
        self._pair = pair
        self._instrument = instrument

        # One-shot state for async audit response
        self._pending_event: threading.Event | None = None
        self._remote_bars: list[dict[str, Any]] = []
        self._lock = threading.Lock()

        # Register our handler once; we'll reuse it with fresh state each check
        self._gateway.on_audit_response(self._on_audit_response)

    def check_parity(self, hours_back: int = 5) -> ParityResult:
        """Perform a parity check for the last N hours of bars.

        Blocks until the audit response arrives or times out (10s).
        """
        bars_back = hours_back * 60  # 1m bars

        # 1. Get local bars
        local_bars = self._data_source.load_historical_bars("1m", pair=self._pair)
        if not local_bars:
            return self._error_result("No local bars available")

        recent_local = local_bars[-bars_back:] if len(local_bars) > bars_back else list(local_bars)

        # 2. Set up one-shot async capture
        with self._lock:
            self._pending_event = threading.Event()
            self._remote_bars = []

        # 3. Send audit request
        self._gateway.send_audit_request(bars_back=bars_back, instrument=self._instrument)

        # 4. Wait for response
        if not self._pending_event.wait(timeout=10.0):
            return self._error_result("Audit response timeout — NinjaTrader did not respond")

        # 5. Compare
        with self._lock:
            remote = list(self._remote_bars)

        if not remote:
            return self._error_result("Audit response empty — no bars returned from NinjaTrader")

        self.logger.info(
            f"[ParityCheck] Comparing {len(recent_local)} local vs {len(remote)} remote bars"
        )

        result = self._checker.check(recent_local, remote)

        # Log summary
        if result.all_good:
            self.logger.info(f"[ParityCheck] {result.summary}")
        else:
            self.logger.warning(f"[ParityCheck] {result.summary}")
            for g in result.gaps:
                if not g.is_market_closed:
                    self.logger.warning(
                        f"[ParityCheck]   {g.gap_type.upper()} at t={g.start_time} — {g.details}"
                    )

        return result

    def _on_audit_response(self, payload: dict[str, Any]) -> None:
        """Called by TradingGateway when AUDIT_RESPONSE arrives."""
        try:
            raw_bars = payload.get("bars", [])
            parsed: list[dict[str, Any]] = []
            for raw in raw_bars:
                parsed.append(
                    {
                        "time": int(raw["time"]),
                        "open": float(raw["open"]),
                        "high": float(raw["high"]),
                        "low": float(raw["low"]),
                        "close": float(raw["close"]),
                        "volume": int(raw.get("volume", 0)),
                        "pair": raw.get("pair", ""),
                    }
                )
            with self._lock:
                self._remote_bars = parsed
        except Exception as e:
            self.logger.error(f"[ParityCheck] Failed to parse audit response: {e}")
            with self._lock:
                self._remote_bars = []
        finally:
            if self._pending_event is not None:
                self._pending_event.set()

    def _error_result(self, message: str) -> ParityResult:
        """Build a ParityResult for error cases."""
        self.logger.error(f"[ParityCheck] {message}")
        return ParityResult(
            checked_at=int(time.time()),
            bars_checked=0,
            gaps_found=0,
            gaps=[],
            all_good=False,
            summary=message,
        )
