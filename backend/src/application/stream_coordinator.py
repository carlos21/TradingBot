"""Coordinator that manages one ``StreamingSession`` per instrument.

The ``StreamCoordinator`` creates sessions lazily when Socket.IO clients join
an instrument room, routes incoming market data by ``pair``, and stops
sessions when they no longer have clients.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

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
        default_symbol: str | None = None,
        stop_grace_sec: float = 5.0,
        stream_activator: Callable[[Instrument], None] | None = None,
    ):
        self._registry = instrument_registry
        self._session_factory = session_factory
        self._default_symbol = default_symbol
        self._stop_grace_sec = stop_grace_sec
        self._activator = stream_activator

        self._sessions: dict[str, StreamingSession] = {}
        self._session_lock = threading.RLock()
        self._stop_timers: dict[str, threading.Timer] = {}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_or_create_instrument(self, symbol: str) -> Instrument | None:
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
        """Add a client to an instrument room and start streaming if needed."""
        with self._session_lock:
            self._cancel_stop_timer(symbol)
            session = self.get_or_create_session(symbol)
            session.join_client(sid)
            if not session._started:
                session.start()
                # Ask the data source to subscribe a non-default instrument so
                # its bars/history actually reach this session.
                if self._activator is not None and symbol != self._default_symbol:
                    try:
                        self._activator(session.instrument)
                    except Exception as e:
                        logger.error(f"stream activator failed for {symbol}: {e}")
            return session

    def leave_instrument(self, symbol: str, sid: str) -> None:
        """Remove a client from an instrument room; schedule stop if empty."""
        with self._session_lock:
            session = self._sessions.get(symbol)
            if session is None:
                return
            session.leave_client(sid)
            if session.client_count() == 0 and session._started:
                self._schedule_stop(symbol)

    def _cancel_stop_timer(self, symbol: str) -> None:
        timer = self._stop_timers.pop(symbol, None)
        if timer is not None:
            timer.cancel()

    def _schedule_stop(self, symbol: str) -> None:
        self._cancel_stop_timer(symbol)

        def _stop():
            with self._session_lock:
                self._stop_timers.pop(symbol, None)
                session = self._sessions.get(symbol)
                if session is None:
                    return
                if session.client_count() > 0:
                    return
                session.stop()
                self._sessions.pop(symbol, None)

        timer = threading.Timer(self._stop_grace_sec, _stop)
        timer.daemon = True
        timer.start()
        self._stop_timers[symbol] = timer

    def stop_all(self) -> None:
        """Stop all sessions immediately (used on shutdown)."""
        with self._session_lock:
            for timer in self._stop_timers.values():
                timer.cancel()
            self._stop_timers.clear()
            for session in list(self._sessions.values()):
                session.stop()
            self._sessions.clear()

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

    def _pair_symbol(self, payload: dict) -> str:
        return payload.get("pair") or self._default_symbol or "MNQ"

    def route_bar(self, bar: dict) -> None:
        """Forward a completed bar to the matching session."""
        symbol = self._pair_symbol(bar)
        session = self.get_session(symbol)
        if session is not None:
            session.on_bar(bar)
        else:
            logger.debug(f"No active session for bar pair={symbol}")

    def route_tick(self, tick: dict) -> None:
        """Forward a tick to the matching session."""
        symbol = self._pair_symbol(tick)
        session = self.get_session(symbol)
        if session is not None:
            session.on_tick(tick)

    def route_partial_bar(self, partial: dict) -> None:
        """Forward a partial bar to the matching session."""
        symbol = self._pair_symbol(partial)
        session = self.get_session(symbol)
        if session is not None:
            session.on_partial_bar(partial)

    def route_history_loaded(self, bars: list[dict]) -> None:
        """Forward a completed history batch to the matching session(s).

        The data source sends one HISTORY_END event per instrument, so we route
        by the last bar's pair when available; otherwise we broadcast to all
        active sessions.
        """
        if bars:
            symbol = bars[-1].get("pair")
            if symbol:
                session = self.get_session(symbol)
                if session is not None:
                    session.on_history_loaded(bars)
                    return
        # Fallback: broadcast to all sessions (legacy / unexpected shape)
        for session in list(self._sessions.values()):
            session.on_history_loaded(bars)

    def route_refresh_start(self, pair: str | None = None) -> None:
        """Forward a refresh-start event to the matching session."""
        symbol = pair or self._default_symbol
        if symbol is None:
            for session in list(self._sessions.values()):
                session.on_refresh_start()
            return
        session = self.get_session(symbol)
        if session is not None:
            session.on_refresh_start()

    def route_before_refresh(self, pair: str | None = None) -> None:
        """Forward a before-refresh reset to the matching session."""
        symbol = pair or self._default_symbol
        if symbol is None:
            for session in list(self._sessions.values()):
                session.on_before_refresh()
            return
        session = self.get_session(symbol)
        if session is not None:
            session.on_before_refresh()

    def route_gap_detected(self, gap_seconds: int, context: str, pair: str | None = None) -> None:
        symbol = pair or self._default_symbol
        if symbol is None:
            return
        session = self.get_session(symbol)
        if session is not None:
            session.on_gap_detected(gap_seconds, context)

    def route_heartbeat_stale(self, age_seconds: float, pair: str | None = None) -> None:
        symbol = pair or self._default_symbol
        if symbol is None:
            return
        session = self.get_session(symbol)
        if session is not None:
            session.on_heartbeat_stale(age_seconds)

    def route_late_history_batch(self, bar_count: int, pair: str | None = None) -> None:
        symbol = pair or self._default_symbol
        if symbol is None:
            return
        session = self.get_session(symbol)
        if session is not None:
            session.on_late_history_batch(bar_count)

    # ------------------------------------------------------------------
    # Helpers used by Socket.IO handlers
    # ------------------------------------------------------------------

    def get_default_symbol(self) -> str:
        """Return the configured default instrument symbol."""
        if self._default_symbol:
            return self._default_symbol
        if self._registry is not None:
            instruments = self._registry.get_all()
            if instruments:
                return instruments[0].symbol
        return "MNQ"

    def require_session(self, symbol: str | None) -> StreamingSession:
        """Resolve a symbol to an active session, defaulting when missing."""
        if not symbol:
            symbol = self.get_default_symbol()
        return self.get_or_create_session(symbol)
