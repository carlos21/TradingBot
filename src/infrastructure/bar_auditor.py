"""
Bar Auditor — verifies that Python's cached bars match NinjaTrader's bars exactly.

Architecture:
    - IBarAuditor      : protocol (interface)
    - IBarComparer     : protocol (interface)
    - BarMismatch      : value object (immutable)
    - AuditResult      : value object (immutable)
    - BarComparer      : pure function class (IBarComparer)
    - NinjaTraderBarAuditor : implementation (IBarAuditor)

Usage (in app_factory):
    from src.infrastructure.bar_auditor import NinjaTraderBarAuditor, BarComparer

    auditor = NinjaTraderBarAuditor(
        gateway=data_source.gateway,
        data_source=data_source,
        logger=logger,
        interval_minutes=5,
        bars_back=60,
        on_drift=lambda result: logger.error(f"DRIFT: {result.summary}"),
    )
    auditor.start()
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from src.infrastructure.gateway.datasource import ZMQDataSource
    from src.infrastructure.gateway.gateway import TradingGateway

from src.utils.app_logger import ILogger


# ═══════════════════════════════════════════════════════════════════════════════
# Value Objects
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BarMismatch:
    """A single bar discrepancy between local and remote."""

    time: int
    local_bar: dict[str, Any] | None
    remote_bar: dict[str, Any] | None
    field_differences: dict[str, tuple[Any, Any]]

    def __str__(self) -> str:
        if self.local_bar is None:
            return f"MISSING  t={self.time}  remote={self._fmt(self.remote_bar)}"
        if self.remote_bar is None:
            return f"EXTRA    t={self.time}  local={self._fmt(self.local_bar)}"
        fields = ", ".join(
            f"{k}: {self.local_bar.get(k)} != {rv}"
            for k, rv in self.field_differences.items()
        )
        return f"MISMATCH t={self.time}  {fields}"

    @staticmethod
    def _fmt(bar: dict[str, Any] | None) -> str:
        if bar is None:
            return "None"
        return f"O={bar.get('open')} H={bar.get('high')} L={bar.get('low')} C={bar.get('close')} V={bar.get('volume')}"


@dataclass(frozen=True)
class AuditResult:
    """Result of comparing two bar sequences."""

    has_drift: bool
    missing_count: int
    extra_count: int
    mismatch_count: int
    details: list[BarMismatch]
    summary: str


# ═══════════════════════════════════════════════════════════════════════════════
# Interfaces
# ═══════════════════════════════════════════════════════════════════════════════

class IBarComparer(Protocol):
    """Pure function interface for comparing two bar lists."""

    def compare(self, local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> AuditResult:
        ...


class IBarAuditor(Protocol):
    """Lifecycle interface for background bar auditing."""

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Implementation: BarComparer
# ═══════════════════════════════════════════════════════════════════════════════

class BarComparer:
    """
    Compare two lists of bars by their 'time' key.

    For every timestamp in the union of both sets:
        - only in local  → extra bar
        - only in remote → missing bar
        - in both but OHLCV differ → mismatch (logs every differing field)
    """

    FIELDS = ("open", "high", "low", "close", "volume")

    def compare(self, local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> AuditResult:
        local_by_time: dict[int, dict[str, Any]] = {b["time"]: b for b in local}
        remote_by_time: dict[int, dict[str, Any]] = {b["time"]: b for b in remote}
        all_times = sorted(set(local_by_time) | set(remote_by_time))

        details: list[BarMismatch] = []
        for t in all_times:
            l_bar = local_by_time.get(t)
            r_bar = remote_by_time.get(t)
            if l_bar is None:
                details.append(BarMismatch(t, None, r_bar, {}))
            elif r_bar is None:
                details.append(BarMismatch(t, l_bar, None, {}))
            else:
                diffs: dict[str, tuple[Any, Any]] = {}
                for f in self.FIELDS:
                    lv = l_bar.get(f)
                    rv = r_bar.get(f)
                    if lv != rv:
                        diffs[f] = (lv, rv)
                if diffs:
                    details.append(BarMismatch(t, l_bar, r_bar, diffs))

        missing = [d for d in details if d.remote_bar is None]
        extra = [d for d in details if d.local_bar is None]
        mismatched = [d for d in details if d.field_differences]

        summary = (
            f"missing={len(missing)} extra={len(extra)} "
            f"mismatched={len(mismatched)} total={len(details)}"
        )

        return AuditResult(
            has_drift=bool(details),
            missing_count=len(missing),
            extra_count=len(extra),
            mismatch_count=len(mismatched),
            details=details,
            summary=summary,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Implementation: NinjaTraderBarAuditor
# ═══════════════════════════════════════════════════════════════════════════════

class NinjaTraderBarAuditor:
    """
    Background auditor that periodically requests recent bars from NinjaTrader
    and compares them against Python's cached bars.

    Thread-safe.  Runs entirely in a background timer thread.
    """

    def __init__(
        self,
        gateway: TradingGateway,
        data_source: ZMQDataSource,
        logger: ILogger,
        interval_minutes: int = 5,
        bars_back: int = 60,
        comparer: IBarComparer | None = None,
        on_drift: Callable[[AuditResult], None] | None = None,
    ):
        self._gateway = gateway
        self._data_source = data_source
        self._logger = logger
        self._interval = max(1, interval_minutes)
        self._bars_back = max(1, bars_back)
        self._comparer = comparer or BarComparer()
        self._on_drift = on_drift

        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()

        # One-shot event + buffer for the async audit response
        self._pending_event: threading.Event | None = None
        self._remote_bars: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start background auditing."""
        with self._lock:
            if self._running:
                return
            self._running = True

        self._gateway.on_audit_response(self._on_audit_response)
        self._schedule_next()
        self._logger.info(f"[BarAuditor] Started (interval={self._interval}m, bars_back={self._bars_back})")

    def stop(self) -> None:
        """Stop background auditing."""
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
        self._logger.info("[BarAuditor] Stopped")

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _schedule_next(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._timer = threading.Timer(self._interval * 60.0, self._run_audit)
            self._timer.daemon = True
            self._timer.start()

    # ------------------------------------------------------------------
    # Audit run
    # ------------------------------------------------------------------

    def _run_audit(self) -> None:
        try:
            local_bars = self._data_source.load_historical_bars("1m")
            if not local_bars:
                self._logger.warning("[BarAuditor] No local bars to audit")
                return

            recent_local = local_bars[-self._bars_back :] if len(local_bars) > self._bars_back else list(local_bars)

            # Fire-and-forget async request
            self._pending_event = threading.Event()
            self._remote_bars = []
            self._gateway.send_audit_request(self._bars_back)

            # Wait up to 10s for the response
            if not self._pending_event.wait(timeout=10.0):
                self._logger.warning("[BarAuditor] Audit response timeout")
                return

            result = self._comparer.compare(recent_local, self._remote_bars)

            if result.has_drift:
                self._logger.error(f"[BarAuditor] DRIFT DETECTED: {result.summary}")
                for d in result.details[:10]:  # cap log noise
                    self._logger.error(f"[BarAuditor]   {d}")
                if len(result.details) > 10:
                    self._logger.error(f"[BarAuditor]   ... and {len(result.details) - 10} more")
                if self._on_drift:
                    try:
                        self._on_drift(result)
                    except Exception as cb_err:
                        self._logger.error(f"[BarAuditor] on_drift callback failed: {cb_err}")
            else:
                self._logger.info(f"[BarAuditor] OK — {len(recent_local)} bars match exactly")

        except Exception as e:
            self._logger.error(f"[BarAuditor] Audit run failed: {e}")
        finally:
            self._schedule_next()

    # ------------------------------------------------------------------
    # Async response handler
    # ------------------------------------------------------------------

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
            self._remote_bars = parsed
        except Exception as e:
            self._logger.error(f"[BarAuditor] Failed to parse audit response: {e}")
            self._remote_bars = []
        finally:
            if self._pending_event is not None:
                self._pending_event.set()
