"""Integration tests for StreamCoordinator session creation behaviour.

Account eligibility used to gate session activation.  The coordinator now
always creates and starts a charting session when a client joins an
instrument; trading eligibility is enforced separately by the strategy/trade
manager based on account instrument assignments.
"""

from __future__ import annotations

from src.application.stream_coordinator import StreamCoordinator
from src.domain.models import Instrument


class FakeSessionFactory:
    """Minimal session factory that returns a lightweight fake session."""

    def __init__(self):
        self.created = []

    def create_session(self, instrument: Instrument):
        session = FakeStreamingSession(instrument)
        self.created.append(session)
        return session


class FakeStreamingSession:
    """Lightweight stand-in for StreamingSession."""

    def __init__(self, instrument: Instrument):
        self.instrument = instrument
        self._started = False
        self.clients: set[str] = set()

    def join_client(self, sid: str) -> None:
        self.clients.add(sid)

    def leave_client(self, sid: str) -> None:
        self.clients.discard(sid)

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False


def test_join_instrument_starts_session_and_subscribes_for_any_symbol():
    factory = FakeSessionFactory()
    activator_calls = []
    coordinator = StreamCoordinator(
        instrument_registry=None,
        session_factory=factory,
        stream_activator=lambda instrument: activator_calls.append(instrument.symbol),
    )

    session = coordinator.join_instrument("MES", "sid-1")

    assert len(factory.created) == 1
    assert session is factory.created[0]
    assert session._started is True
    assert "sid-1" in session.clients
    assert activator_calls == ["MES"]


def test_join_instrument_does_not_restart_an_already_running_session():
    factory = FakeSessionFactory()
    activator_calls = []
    coordinator = StreamCoordinator(
        instrument_registry=None,
        session_factory=factory,
        stream_activator=lambda instrument: activator_calls.append(instrument.symbol),
    )

    coordinator.join_instrument("MNQ", "sid-1")
    coordinator.join_instrument("MNQ", "sid-2")

    assert len(factory.created) == 1
    assert factory.created[0]._started is True
    assert factory.created[0].clients == {"sid-1", "sid-2"}
    assert activator_calls == ["MNQ"]


def test_join_instrument_without_activator_still_starts_session():
    factory = FakeSessionFactory()
    coordinator = StreamCoordinator(
        instrument_registry=None,
        session_factory=factory,
    )

    session = coordinator.join_instrument("ES", "sid-1")

    assert len(factory.created) == 1
    assert session._started is True
    assert "sid-1" in session.clients
