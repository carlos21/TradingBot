"""Supervise platform subscriptions that never receive a first response.

When an instrument is subscribed (subscribe + history refresh sent), the
platform is expected to answer with REFRESH_START / HISTORY_BATCH.  If it
never does — e.g. the NinjaTrader connector's command channel is wedged while
heartbeats keep flowing — the instrument would otherwise wait forever.  The
``SubscriptionSupervisor`` tracks such instruments and retries
subscribe+refresh with exponential backoff until the platform answers, the
instrument is unrequested, or the connection drops.

All collaborators are injected via constructor protocols; the supervisor
knows nothing about the gateway or the data source.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from src.utils.app_logger import ILogger


class ISubscriptionTransport(Protocol):
    """Port for sending subscribe+refresh commands to the platform."""

    def subscribe(self, instrument: str) -> None:
        """Send a subscribe command for the instrument's full name."""
        ...

    def refresh(self, days: int, instrument: str) -> None:
        """Send a history refresh request for the instrument's full name."""
        ...


class IAlertNotifier(Protocol):
    """Port for user-facing alerts."""

    def send(self, message: str) -> None:
        """Deliver an alert message to the user."""
        ...


class SubscriptionSupervisor:
    """Retry subscribe+refresh for instruments the platform never answers.

    Instruments are tracked by full name after their first subscribe+refresh
    is sent.  A single daemon timer drives retry cycles with exponential
    backoff (``base * 2**attempt``, capped at ``max_delay_sec``); each cycle
    re-sends subscribe+refresh for every still-awaiting instrument and
    invokes ``on_retry(instrument, attempt)``.  At ``escalate_after`` a
    one-shot notifier alert is sent; retries then continue at the capped
    delay.
    """

    def __init__(
        self,
        transport: ISubscriptionTransport,
        notifier: IAlertNotifier,
        on_retry: Callable[[Any, int], None] | None,
        logger: ILogger,
        days: int = 1,
        base_delay_sec: float = 10.0,
        max_delay_sec: float = 60.0,
        escalate_after: int = 3,
        fail_fast_cooldown_sec: float = 0.5,
    ):
        """
        Initialize the supervisor.

        Args:
            transport: Sends subscribe/refresh commands to the platform.
            notifier: Delivers the escalation alert.
            on_retry: Callback ``(instrument, attempt)`` invoked for every
                retried instrument (used to surface the retry to sessions).
            logger: Logger instance (required).
            days: History depth (whole days) sent with each refresh request.
            base_delay_sec: First retry delay; doubles every attempt.
            max_delay_sec: Backoff cap.
            escalate_after: Attempt at which the notifier alert is sent.
            fail_fast_cooldown_sec: Minimum seconds between immediate retry
                cycles triggered by ``fail_fast``. Bursts of gateway command
                timeouts within this window are coalesced into a single retry.
        """
        self._transport = transport
        self._notifier = notifier
        self._on_retry = on_retry
        self._logger = logger
        self._days = days
        self._base_delay_sec = base_delay_sec
        self._max_delay_sec = max_delay_sec
        self._escalate_after = escalate_after
        self._fail_fast_cooldown_sec = fail_fast_cooldown_sec

        # Instruments awaiting the platform's first response, by full name.
        self._awaiting: dict[str, Any] = {}
        self._attempt: int = 0
        self._escalated: bool = False
        self._timer: threading.Timer | None = None
        self._stopped: bool = False
        self._last_retry_cycle_time: float = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def track(self, instrument: Any) -> None:
        """Start watching an instrument for the platform's first response.

        Idempotent per ``full_name``.  Arms the retry timer when this is the
        first awaiting instrument.
        """
        full_name = getattr(instrument, "full_name", None)
        if not full_name:
            return
        with self._lock:
            # A track() after stop() starts a fresh episode (e.g. reconnect).
            self._stopped = False
            if full_name in self._awaiting:
                return
            self._awaiting[full_name] = instrument
            if self._timer is None:
                self._schedule_locked()

    def mark_responded(self, full_name: str) -> None:
        """Stop tracking an instrument the platform has answered."""
        self._remove(full_name)

    def untrack(self, full_name: str) -> None:
        """Stop tracking an instrument that is no longer requested."""
        self._remove(full_name)

    def stop(self) -> None:
        """Cancel every pending retry (e.g. on platform disconnect).

        Armed timers never fire again after this; a later ``track()`` starts
        a fresh episode with reset backoff.
        """
        with self._lock:
            self._stopped = True
            self._cancel_timer_locked()
            self._awaiting.clear()
            self._attempt = 0
            self._escalated = False
            self._last_retry_cycle_time = 0.0

    def fail_fast(self, full_name: str) -> None:
        """Run an immediate retry cycle for a tracked instrument.

        Used when the gateway reports an ACK timeout instead of waiting for
        the backoff timer to expire.  Calls that arrive while a retry cycle
        has just run are coalesced so a burst of timed-out commands for the
        same instrument does not generate a burst of retries.
        """
        with self._lock:
            if self._stopped or full_name not in self._awaiting:
                return
            now = time.time()
            if now - self._last_retry_cycle_time < self._fail_fast_cooldown_sec:
                self._logger.debug(
                    f"fail_fast for {full_name} coalesced within cooldown"
                )
                return
            self._last_retry_cycle_time = now
            self._cancel_timer_locked()
            self._attempt += 1
            attempt = self._attempt
            instruments = list(self._awaiting.values())
        self._run_retry_cycle(instruments, attempt)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _remove(self, full_name: str) -> None:
        with self._lock:
            self._awaiting.pop(full_name, None)
            if not self._awaiting:
                self._cancel_timer_locked()
                self._attempt = 0
                self._escalated = False
                self._last_retry_cycle_time = 0.0

    def _schedule_locked(self) -> None:
        """Arm the next retry timer.  Caller must hold ``_lock``."""
        self._cancel_timer_locked()
        delay = min(
            self._base_delay_sec * (2 ** self._attempt),
            self._max_delay_sec,
        )
        timer = threading.Timer(delay, self._on_retry_timer)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_retry_timer(self) -> None:
        with self._lock:
            self._timer = None
            if self._stopped or not self._awaiting:
                return
            self._attempt += 1
            attempt = self._attempt
            instruments = list(self._awaiting.values())
        self._run_retry_cycle(instruments, attempt)

    def _run_retry_cycle(self, instruments: list[Any], attempt: int) -> None:
        """Re-send subscribe+refresh for the given still-awaiting instruments."""
        with self._lock:
            self._last_retry_cycle_time = time.time()
        names = ", ".join(sorted(i.full_name for i in instruments))
        self._logger.warning(
            f"No platform response after subscribe — retrying subscribe+refresh "
            f"for [{names}] (attempt {attempt})"
        )
        if attempt >= self._escalate_after and not self._escalated:
            # The platform is connected (heartbeats flow) but never answers
            # commands — its command channel is most likely wedged. Retries
            # may still heal it; make sure the user knows where to look.
            self._escalated = True
            try:
                self._notifier.send(
                    f"⚠️ NinjaTrader connector is not responding to commands "
                    f"(attempt {attempt}). Check NinjaTrader / restart the connector."
                )
            except Exception as e:
                self._logger.error(f"Failed to send notifier alert: {e}")
        for instrument in instruments:
            full_name = instrument.full_name
            with self._lock:
                if self._stopped or full_name not in self._awaiting:
                    continue
            try:
                self._transport.subscribe(full_name)
                self._transport.refresh(self._days, full_name)
            except Exception as e:
                self._logger.error(f"Subscription retry failed for {full_name}: {e}")
            if self._on_retry is not None:
                try:
                    self._on_retry(instrument, attempt)
                except Exception as e:
                    self._logger.error(f"on_retry callback failed for {full_name}: {e}")
        with self._lock:
            if not self._stopped and self._awaiting and self._timer is None:
                self._schedule_locked()
