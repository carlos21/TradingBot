"""Coordinates data-source events, warm-up, and readiness transitions."""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any, Callable

from src.application.ports import EventPublisher
from src.domain.readiness import ReadinessState, ReadinessStateMachine
from src.domain.readiness.protocols import IWarmupPolicy
from src.utils.app_logger import ILogger

from .live_bar_buffer import LiveBarBuffer
from .warmup_orchestrator import WarmupOrchestrator

if TYPE_CHECKING:
    from src.infrastructure.data_sources.combined_datasource import CombinedDataSource


class ReadinessMonitor:
    """
    Single coordinator that turns data-source events into readiness state
    transitions.

    Responsibilities:
      - move the state machine through WAITING_FOR_HISTORY -> REFRESHING ->
        WARMING_UP -> READY/LIVE
      - run the WarmupOrchestrator after a non-empty history load
      - buffer live bars that arrive during a real warm-up
      - drop live bars when history has not completed
      - schedule history retries when the platform returns no historical data
    """

    def __init__(
        self,
        state_machine: ReadinessStateMachine,
        warmup_orchestrator: WarmupOrchestrator,
        warmup_policy: IWarmupPolicy,
        bar_buffer: LiveBarBuffer,
        live_bar_processor: Callable[[dict[str, Any]], None],
        data_source: CombinedDataSource | None = None,
        socketio_publisher: EventPublisher | None = None,
        history_loaded_emitter: Callable[[Any, dict[str, Any]], None] | None = None,
        logger: ILogger | None = None,
        retry_base_delay_sec: float = 2.0,
        retry_max_delay_sec: float = 60.0,
    ) -> None:
        self._state_machine = state_machine
        self._warmup_orchestrator = warmup_orchestrator
        self._warmup_policy = warmup_policy
        self._bar_buffer = bar_buffer
        self._live_bar_processor = live_bar_processor
        self._data_source = data_source
        self._socketio_publisher = socketio_publisher
        self._history_loaded_emitter = history_loaded_emitter
        self._logger = logger
        self._pair: str = ""
        self._retry_base_delay_sec = retry_base_delay_sec
        self._retry_max_delay_sec = retry_max_delay_sec
        self._retry_timer: threading.Timer | None = None
        self._retry_lock = threading.Lock()
        self._warmup_in_progress: bool = False
        self._warmup_thread: threading.Thread | None = None

    def set_pair(self, pair: str) -> None:
        self._pair = pair

    def on_refresh_start(self) -> None:
        """Called when the platform starts sending a fresh history batch."""
        self._cancel_retry_timer()
        self._cancel_warmup()
        self._state_machine.start_refresh()
        self._bar_buffer.clear()
        if self._logger:
            self._logger.info("[Readiness] Refresh started — buffering live bars")

    def on_history_complete(self, bars: list[dict[str, Any]]) -> None:
        """Called when the full historical bar set has been received."""
        self._cancel_retry_timer()
        self._cancel_warmup()

        if not bars:
            self._state_machine.history_empty()
            self._schedule_history_retry()
            return

        self._state_machine.history_loaded()

        # Notify the frontend that historical bars are available for display,
        # even if the data is not yet fresh enough for live trading.
        if self._socketio_publisher is not None:
            payload = {
                "readiness_state": self._state_machine.state.name,
                "readiness_reason": self._state_machine.reason,
                "bar_count": len(bars),
            }
            with contextlib.suppress(Exception):
                if self._history_loaded_emitter is not None:
                    self._history_loaded_emitter(self._socketio_publisher, payload)
                else:
                    self._socketio_publisher.emit("history_loaded", payload)

        # Run warmup in a background thread so the gateway receive loop is not
        # blocked. Blocking the receive loop prevents heartbeat processing and
        # causes a heartbeat timeout → disconnect → reconnect → refresh loop.
        self._warmup_orchestrator.reset_cancel()
        self._warmup_in_progress = True
        bars_snapshot = list(bars)
        pair_snapshot = self._pair

        def _run_warmup() -> None:
            try:
                self._warmup_orchestrator.run(bars_snapshot, pair_snapshot)
            finally:
                self._warmup_in_progress = False
                self._try_warmup_complete()

        self._warmup_thread = threading.Thread(target=_run_warmup, daemon=True, name="WarmupReplay")
        self._warmup_thread.start()

    def on_live_bar(self, bar: dict[str, Any]) -> None:
        """Called for each live bar (completed or partial)."""
        state = self._state_machine.state

        # Partial bars are UI-only updates: never buffer them and never feed
        # them to the strategy. Process them immediately so the chart reflects
        # the current forming candle while the system warms up.
        if bar.get("partial"):
            self._live_bar_processor(bar)
            return

        # History never completed — do not let live bars stream to the strategy.
        if state in (ReadinessState.CONNECTED, ReadinessState.WAITING_FOR_HISTORY):
            return

        if state in (ReadinessState.REFRESHING, ReadinessState.WARMING_UP):
            self._bar_buffer.append(bar)
            if state == ReadinessState.WARMING_UP:
                self._try_warmup_complete()
            return

        if state == ReadinessState.DEGRADED:
            self._live_bar_processor(bar)
            if self._warmup_policy.is_warm(self._warmup_orchestrator.strategy):
                self._state_machine.recover()
            return

        # READY or LIVE
        if state == ReadinessState.READY:
            self._state_machine.live_bar_received()
        self._live_bar_processor(bar)

    def on_late_history_batch(self, bar_count: int) -> None:
        """Called when gap-fill HISTORY_BATCH arrives after READY/LIVE.

        If a significant batch arrives, the indicators may have been warmed
        on incomplete data.  Degrade so the warmup policy can re-check.
        """
        state = self._state_machine.state
        if state not in (ReadinessState.READY, ReadinessState.LIVE):
            return
        if bar_count > 0:
            if self._logger:
                self._logger.warning(
                    f"[Readiness] Late gap-fill: {bar_count} bar(s) arrived after {state.name} — "
                    "degrading to re-check indicator warmth"
                )
            self._state_machine.degrade(
                f"Gap-fill received {bar_count} bars after {state.name}"
            )

    def on_gap_detected(self, gap_seconds: int, context: str) -> None:
        """Called when the data source detects a suspicious gap."""
        self._state_machine.degrade(
            f"{gap_seconds}s gap detected ({context})"
        )

    def on_heartbeat_stale(self, age_seconds: float) -> None:
        """Called when no completed bar has arrived for too long."""
        self._state_machine.degrade(
            f"Live bar stream stale: {age_seconds:.0f}s since last bar"
        )

    def stop(self) -> None:
        """Cancel any pending retry timer and in-progress warmup."""
        self._cancel_retry_timer()
        self._cancel_warmup()

    def _cancel_warmup(self) -> None:
        """Cancel any in-progress warmup and wait for its thread to finish."""
        self._warmup_orchestrator.cancel()
        thread = self._warmup_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._warmup_thread = None
        self._warmup_in_progress = False

    def _schedule_history_retry(self) -> None:
        """Schedule another history refresh after a backoff delay."""
        request_refresh = getattr(self._data_source, "request_refresh", None)
        if request_refresh is None:
            if self._logger:
                self._logger.warning(
                    "[Readiness] Cannot request history retry: data source "
                    "has no request_refresh method"
                )
            return

        attempt = self._state_machine.retry_count
        delay = min(
            self._retry_base_delay_sec * (2 ** (attempt - 1)),
            self._retry_max_delay_sec,
        )

        if self._logger:
            self._logger.info(
                f"[Readiness] Scheduling history retry #{attempt} in {delay:.1f}s"
            )

        def _retry() -> None:
            with self._retry_lock:
                self._retry_timer = None
            try:
                request_refresh()
                self._state_machine.history_retry_scheduled()
            except Exception as e:
                # Request failed — don't count as an attempt so backoff
                # doesn't grow when the request never reached the platform
                if self._logger:
                    self._logger.error(
                        f"[Readiness] History retry request failed: {e}"
                    )
                # Re-schedule with the same attempt count
                self._schedule_history_retry()

        with self._retry_lock:
            old_timer = self._retry_timer
            self._retry_timer = None
            if old_timer is not None:
                old_timer.cancel()
            self._retry_timer = threading.Timer(delay, _retry)
            self._retry_timer.daemon = True
            self._retry_timer.start()

    def _cancel_retry_timer(self) -> None:
        timer = None
        with self._retry_lock:
            timer = self._retry_timer
            self._retry_timer = None
        if timer is not None:
            timer.cancel()

    def _try_warmup_complete(self) -> None:
        """Transition to READY if data is fresh and indicators are warm."""
        # Don't check while the warmup thread is still replaying bars.
        if self._warmup_in_progress:
            return
        state = self._state_machine.state
        # Only transition from WARMING_UP; if a refresh started after warmup
        # kicked off, we're in a different state and must not interfere.
        if state != ReadinessState.WARMING_UP:
            return

        if self._data_source is not None:
            complete, reason = self._data_source.check_history_completeness()
            if not complete:
                if self._logger:
                    self._logger.info(f"[Readiness] History not ready: {reason}")
                return

        if not self._warmup_policy.is_warm(self._warmup_orchestrator.strategy):
            return
        transitioned = self._state_machine.warmup_complete()
        if transitioned:
            self._bar_buffer.flush()
            if self._logger:
                self._logger.info(
                    "[Readiness] Warmup complete — ready for live trading"
                )
            if self._socketio_publisher is not None:
                with contextlib.suppress(Exception):
                    self._socketio_publisher.emit(
                        "trading_ready",
                        {
                            "readiness_state": self._state_machine.state.name,
                            "readiness_reason": self._state_machine.reason,
                        },
                    )

    def get_health(self) -> dict[str, Any]:
        """Return readiness-specific health fields."""
        return {
            "readiness_state": self._state_machine.state.name,
            "readiness_reason": self._state_machine.reason,
        }
