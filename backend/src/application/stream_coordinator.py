"""Coordinator that manages one ``StreamingSession`` per instrument.

The ``StreamCoordinator`` creates sessions lazily when Socket.IO clients join
an instrument room and routes incoming market data by ``pair``.  Sessions
keep running when their last client leaves — they stop only on stream stop /
shutdown (``stop_all``), never on tab close.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol

from src.domain.models import Instrument
from src.services.instrument_registry import IInstrumentRegistry

from .streaming_session import StreamingSession

logger = logging.getLogger(__name__)


class ISessionFactory(Protocol):
    """Port for creating a ``StreamingSession`` for an instrument."""

    def create_session(self, instrument: Instrument) -> StreamingSession:
        """Return a new session for the supplied instrument."""
        ...


class StreamCoordinator:
    """Owns all active streaming sessions and routes data to them by symbol."""

    def __init__(
        self,
        instrument_registry: IInstrumentRegistry | None,
        session_factory: ISessionFactory,
        stream_activator: Callable[[Instrument], None] | None = None,
    ):
        self._registry = instrument_registry
        self._session_factory = session_factory
        self._activator = stream_activator

        self._sessions: dict[str, StreamingSession] = {}
        self._session_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_or_create_instrument(self, symbol: str) -> Instrument:
        """Find a registered instrument by symbol, falling back to defaults."""
        if self._registry is not None:
            for instrument in self._registry.get_all():
                if instrument.symbol == symbol:
                    return instrument
        # Backward compatibility: if the symbol is not registered, build a
        # minimal instrument so that legacy single-pair setups keep working.
        return Instrument(symbol=symbol, full_name=symbol)

    def get_or_create_session(self, symbol: str) -> StreamingSession:
        """Return the session for *symbol*, creating it if necessary."""
        with self._session_lock:
            session = self._sessions.get(symbol)
            if session is None:
                instrument = self._get_or_create_instrument(symbol)
                session = self._session_factory.create_session(instrument)
                self._sessions[symbol] = session
            return session

    def get_session(self, symbol: str) -> StreamingSession | None:
        with self._session_lock:
            return self._sessions.get(symbol)

    def join_instrument(self, symbol: str, sid: str) -> StreamingSession:
        """Add a client to an instrument room and start streaming.

        The session is created (if needed) and the data source is asked to
        subscribe the instrument so chart data loads. Trading eligibility is
        handled separately by the strategy/trade manager based on account
        instrument assignments.
        """
        with self._session_lock:
            session = self.get_or_create_session(symbol)
            session.join_client(sid)
            if not session._started:
                session.start()
                # Ask the data source to subscribe the instrument so its
                # bars/history actually reach this session.
                if self._activator is not None:
                    try:
                        self._activator(session.instrument)
                    except Exception as e:
                        logger.error(f"stream activator failed for {symbol}: {e}")
            return session

    def leave_instrument(self, symbol: str, sid: str) -> None:
        """Remove a client from an instrument room.

        The session keeps streaming when its last client leaves — session
        lifetime is tied to Start/Stop Streaming, not tab presence.
        """
        with self._session_lock:
            session = self._sessions.get(symbol)
            if session is None:
                return
            session.leave_client(sid)

    def stop_all(self) -> None:
        """Stop all sessions immediately (used on stream stop/shutdown)."""
        with self._session_lock:
            for session in list(self._sessions.values()):
                session.stop()
            self._sessions.clear()

    def stop_session(self, symbol: str) -> bool:
        """Stop and remove the session for *symbol* only.

        Other instruments keep streaming.  Returns True when a session was
        stopped, False when no session exists for *symbol*.
        """
        with self._session_lock:
            session = self._sessions.pop(symbol, None)
            if session is None:
                return False
            session.stop()
            return True

    def list_active_symbols(self) -> list[str]:
        """Return symbols of currently active sessions."""
        with self._session_lock:
            return list(self._sessions.keys())

    def get_active_sessions(self) -> list[StreamingSession]:
        """Return currently active session instances."""
        with self._session_lock:
            return list(self._sessions.values())

    # ------------------------------------------------------------------
    # Data routing
    # ------------------------------------------------------------------

    def _payload_symbol(self, payload: dict, kind: str) -> str | None:
        """Extract the routing symbol from a market-data payload.

        There is no default symbol: payloads without a ``pair`` are logged
        and dropped.
        """
        symbol = payload.get("pair")
        if not symbol:
            logger.warning(f"{kind} payload without pair — dropped")
            return None
        return symbol

    def route_bar(self, bar: dict) -> None:
        """Forward a completed bar to the matching session."""
        symbol = self._payload_symbol(bar, "bar")
        if symbol is None:
            return
        session = self.get_session(symbol)
        if session is not None:
            session.on_bar(bar)
        else:
            logger.debug(f"No active session for bar pair={symbol}")

    def route_tick(self, tick: dict) -> None:
        """Forward a tick to the matching session."""
        symbol = self._payload_symbol(tick, "tick")
        if symbol is None:
            return
        session = self.get_session(symbol)
        if session is not None:
            session.on_tick(tick)

    def route_partial_bar(self, partial: dict) -> None:
        """Forward a partial bar to the matching session."""
        symbol = self._payload_symbol(partial, "partial_bar")
        if symbol is None:
            return
        session = self.get_session(symbol)
        if session is not None:
            session.on_partial_bar(partial)

    def route_history_loaded(self, bars: list[dict], pair: str | None = None) -> None:
        """Forward a completed history batch to the matching session.

        The data source sends one HISTORY_END event per instrument; the pair
        is taken from the explicit argument or, failing that, the last bar.
        Batches that cannot be attributed to a pair are logged and dropped.
        """
        symbol = pair or (bars[-1].get("pair") if bars else None)
        if not symbol:
            logger.warning("history_loaded batch without pair — dropped")
            return
        session = self.get_session(symbol)
        if session is not None:
            session.on_history_loaded(bars)

    def route_refresh_start(self, pair: str | None = None) -> None:
        """Forward a refresh-start event to the matching session."""
        if not pair:
            logger.warning("refresh_start without pair — dropped")
            return
        session = self.get_session(pair)
        if session is not None:
            session.on_refresh_start()

    def route_before_refresh(self, pair: str | None = None) -> None:
        """Forward a before-refresh reset to the matching session."""
        if not pair:
            logger.warning("before_refresh without pair — dropped")
            return
        session = self.get_session(pair)
        if session is not None:
            session.on_before_refresh()

    def route_gap_detected(self, gap_seconds: int, context: str, pair: str | None = None) -> None:
        if not pair:
            logger.warning("gap_detected without pair — dropped")
            return
        session = self.get_session(pair)
        if session is not None:
            session.on_gap_detected(gap_seconds, context)

    def route_heartbeat_stale(self, age_seconds: float) -> None:
        """Degrade every session: a stalled bar stream is connection-level."""
        for session in self.get_active_sessions():
            session.on_heartbeat_stale(age_seconds)

    def route_history_retry(self, pair: str, attempt: int) -> None:
        """Forward a subscription retry to the matching session's readiness monitor.

        Sent by the data source's subscription supervisor when the platform
        has not answered subscribe+refresh for ``pair``.
        """
        if not pair:
            logger.warning("history_retry without pair — dropped")
            return
        session = self.get_session(pair)
        if session is not None and session.readiness_monitor is not None:
            session.readiness_monitor.on_platform_unresponsive(attempt)

    def route_late_history_batch(self, bar_count: int, pair: str | None = None) -> None:
        if not pair:
            logger.warning("late_history_batch without pair — dropped")
            return
        session = self.get_session(pair)
        if session is not None:
            session.on_late_history_batch(bar_count)

    # ------------------------------------------------------------------
    # Helpers used by Socket.IO handlers
    # ------------------------------------------------------------------

    def require_session(self, symbol: str) -> StreamingSession:
        """Resolve an explicit symbol to a session, creating it if needed."""
        if not symbol:
            raise ValueError("symbol is required — there is no default instrument")
        return self.get_or_create_session(symbol)
