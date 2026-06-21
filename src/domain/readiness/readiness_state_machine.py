"""Thread-safe readiness state machine."""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any

from src.application.ports import EventPublisher
from src.utils.app_logger import log_timestamp

from .readiness_state import ReadinessState

if TYPE_CHECKING:
    from src.utils.app_logger import ILogger


class ReadinessStateMachine:
    """
    Single source of truth for live-trading readiness.

    The machine exposes explicit transitions.  Callers cannot mutate the state
    directly, which removes the risk of scattered flags getting out of sync.
    """

    def __init__(
        self,
        event_publisher: EventPublisher | None = None,
        logger: ILogger | None = None,
    ) -> None:
        self._state = ReadinessState.DISCONNECTED
        self._reason = "Initializing"
        self._lock = threading.RLock()
        self._event_publisher = event_publisher
        self._logger = logger
        self._observers: list[Any] = []
        self._retry_count = 0

    @property
    def state(self) -> ReadinessState:
        with self._lock:
            return self._state

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    @property
    def retry_count(self) -> int:
        with self._lock:
            return self._retry_count

    def is_ready(self) -> bool:
        with self._lock:
            return self._state in (ReadinessState.READY, ReadinessState.LIVE)

    def add_observer(self, observer: Any) -> None:
        with self._lock:
            if observer not in self._observers:
                self._observers.append(observer)

    def remove_observer(self, observer: Any) -> None:
        with self._lock:
            if observer in self._observers:
                self._observers.remove(observer)

    # ------------------------------------------------------------------
    # Explicit transitions
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        return self._transition(
            ReadinessState.CONNECTED,
            "Platform connected",
        )

    def start_refresh(self) -> bool:
        with self._lock:
            self._retry_count = 0
            return self._transition(
                ReadinessState.REFRESHING,
                "Historical data refresh started",
            )

    def history_empty(self) -> bool:
        """Move CONNECTED/REFRESHING -> WAITING_FOR_HISTORY when no bars arrived."""
        with self._lock:
            self._retry_count += 1
            return self._transition(
                ReadinessState.WAITING_FOR_HISTORY,
                "No historical data received",
            )

    def history_retry_scheduled(self) -> bool:
        """Stay in WAITING_FOR_HISTORY and record that a refresh was requested."""
        with self._lock:
            return self._transition(
                ReadinessState.WAITING_FOR_HISTORY,
                f"History refresh requested (attempt {self._retry_count})",
            )

    def history_loaded(self) -> bool:
        with self._lock:
            self._retry_count = 0
            return self._transition(
                ReadinessState.WARMING_UP,
                "Historical data loaded; warming up indicators",
            )

    def warmup_complete(self) -> bool:
        return self._transition(
            ReadinessState.READY,
            "Indicators warmed and history complete",
        )

    def live_bar_received(self) -> bool:
        """Move from READY to LIVE when the first live bar is processed."""
        with self._lock:
            if self._state == ReadinessState.READY:
                return self._transition(
                    ReadinessState.LIVE,
                    "Live bar stream active",
                )
            return False

    def degrade(self, reason: str) -> bool:
        """Move READY/LIVE -> DEGRADED when data quality drops."""
        with self._lock:
            if self._state in (ReadinessState.READY, ReadinessState.LIVE):
                return self._transition(ReadinessState.DEGRADED, reason)
            return False

    def recover(self) -> bool:
        """Move DEGRADED -> READY if the problem has cleared."""
        with self._lock:
            if self._state == ReadinessState.DEGRADED:
                return self._transition(
                    ReadinessState.READY,
                    "Data quality recovered",
                )
            return False

    def disconnect(self) -> bool:
        return self._transition(
            ReadinessState.DISCONNECTED,
            "Platform disconnected",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Valid state transitions — any transition not listed here is logged as unexpected
    _VALID_TRANSITIONS: dict[ReadinessState, set[ReadinessState]] = {
        ReadinessState.DISCONNECTED: {ReadinessState.CONNECTED},
        # CONNECTED may receive a fresh/complete history batch directly (e.g. after
        # a brief reconnect), so WARMING_UP is a legal target.
        ReadinessState.CONNECTED: {ReadinessState.REFRESHING, ReadinessState.WAITING_FOR_HISTORY, ReadinessState.WARMING_UP, ReadinessState.DISCONNECTED},
        ReadinessState.WAITING_FOR_HISTORY: {ReadinessState.REFRESHING, ReadinessState.WAITING_FOR_HISTORY, ReadinessState.DISCONNECTED},
        ReadinessState.REFRESHING: {ReadinessState.WARMING_UP, ReadinessState.WAITING_FOR_HISTORY, ReadinessState.DISCONNECTED},
        ReadinessState.WARMING_UP: {ReadinessState.READY, ReadinessState.DISCONNECTED},
        ReadinessState.READY: {ReadinessState.LIVE, ReadinessState.DEGRADED, ReadinessState.DISCONNECTED, ReadinessState.CONNECTED, ReadinessState.REFRESHING},
        ReadinessState.LIVE: {ReadinessState.DEGRADED, ReadinessState.DISCONNECTED, ReadinessState.CONNECTED, ReadinessState.REFRESHING},
        ReadinessState.DEGRADED: {ReadinessState.READY, ReadinessState.DEGRADED, ReadinessState.DISCONNECTED, ReadinessState.CONNECTED, ReadinessState.REFRESHING},
    }

    def _transition(
        self,
        new_state: ReadinessState,
        reason: str,
    ) -> bool:
        with self._lock:
            if self._state == new_state and self._reason == reason:
                return False
            # Same state, different reason for DEGRADED: update reason silently
            # to avoid log noise from multiple degradation sources
            if self._state == new_state and new_state == ReadinessState.DEGRADED:
                self._reason = reason
                self._log_transition(self._state, new_state, reason)
                return False
            # Validate transition
            valid = self._VALID_TRANSITIONS.get(self._state, set())
            if new_state not in valid and self._logger is not None:
                with contextlib.suppress(Exception):
                    self._logger.warning(
                        f"[ReadinessStateMachine] Unexpected transition "
                        f"{self._state.name} -> {new_state.name} | reason={reason}"
                    )
            previous_state = self._state
            self._state = new_state
            self._reason = reason
            self._log_transition(previous_state, new_state, reason)
            self._notify(previous_state, reason)
            return True

    def _log_transition(
        self,
        previous_state: ReadinessState,
        new_state: ReadinessState,
        reason: str,
    ) -> None:
        message = (
            f"[ReadinessStateMachine] {previous_state.name} -> {new_state.name}"
            f" | reason={reason}"
        )
        if self._logger is not None:
            with contextlib.suppress(Exception):
                self._logger.info(message)
        else:
            # Fallback for contexts where no logger is injected yet.
            print(f"{log_timestamp()} {message}", flush=True)

    def _notify(self, previous_state: ReadinessState, reason: str) -> None:
        payload = {
            "previous_state": previous_state.name,
            "state": self._state.name,
            "reason": reason,
        }
        if self._event_publisher is not None:
            with contextlib.suppress(Exception):
                self._event_publisher.emit(
                    "readiness_changed",
                    payload,
                )

        # Observer list may be mutated during iteration; snapshot it.
        for observer in list(self._observers):
            with contextlib.suppress(Exception):
                observer.on_readiness_changed(
                    self._state,
                    previous_state,
                    reason,
                )
