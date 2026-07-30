"""Tests for ``src.application.stream_coordinator``."""

from unittest.mock import MagicMock

import pytest

from src.application.stream_coordinator import StreamCoordinator
from src.domain.models import Instrument


class FakeSession:
    def __init__(self, instrument: Instrument):
        self.instrument = instrument
        self.symbol = instrument.symbol
        self._started = False
        self._clients: set[str] = set()
        self.bars: list[dict] = []
        self.ticks: list[dict] = []
        self.partial_bars: list[dict] = []
        self.history_loaded_calls: list[list[dict]] = []

    def start(self):
        self._started = True

    def stop(self):
        self._started = False

    def join_client(self, sid: str):
        self._clients.add(sid)

    def leave_client(self, sid: str):
        self._clients.discard(sid)

    def client_count(self) -> int:
        return len(self._clients)

    def on_bar(self, bar: dict):
        self.bars.append(bar)

    def on_tick(self, tick: dict):
        self.ticks.append(tick)

    def on_partial_bar(self, partial: dict):
        self.partial_bars.append(partial)

    def on_history_loaded(self, bars: list[dict]):
        self.history_loaded_calls.append(bars)


class FakeRegistry:
    def __init__(self, instruments: list[Instrument]):
        self._instruments = instruments

    def get_all(self) -> list[Instrument]:
        return list(self._instruments)

    def save(self, instruments: list[Instrument]) -> None:
        self._instruments = list(instruments)


class FakeSessionFactory:
    def __init__(self):
        self.created: list[Instrument] = []

    def create_session(self, instrument: Instrument) -> FakeSession:
        self.created.append(instrument)
        return FakeSession(instrument)


@pytest.fixture
def registry():
    return FakeRegistry([
        Instrument(symbol="MNQ", full_name="MNQ 09-26"),
        Instrument(symbol="ES", full_name="ES 09-26"),
    ])


@pytest.fixture
def factory():
    return FakeSessionFactory()


@pytest.fixture
def coordinator(registry, factory):
    return StreamCoordinator(
        instrument_registry=registry,
        session_factory=factory,
    )


class TestSessionLifecycle:
    def test_join_creates_session(self, coordinator, factory):
        session = coordinator.join_instrument("MNQ", "sid-1")
        assert session is not None
        assert session.symbol == "MNQ"
        assert session._started is True
        assert "sid-1" in session._clients
        assert len(factory.created) == 1

    def test_join_reuses_existing_session(self, coordinator, factory):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.join_instrument("MNQ", "sid-2")
        assert len(factory.created) == 1
        session = coordinator.get_session("MNQ")
        assert session.client_count() == 2

    def test_leave_keeps_session_streaming_at_zero_clients(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.leave_instrument("MNQ", "sid-1")
        # Sessions persist at zero clients — they stop only via stop_all().
        session = coordinator.get_session("MNQ")
        assert session is not None
        assert session._started is True
        assert session.client_count() == 0
        assert coordinator.list_active_symbols() == ["MNQ"]

    def test_stop_all_stops_and_clears_sessions(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.join_instrument("ES", "sid-2")
        coordinator.stop_all()
        assert coordinator.list_active_symbols() == []
        assert coordinator.get_session("MNQ") is None

    def test_stop_session_stops_and_removes_only_that_session(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.join_instrument("ES", "sid-2")

        stopped = coordinator.stop_session("ES")

        assert stopped is True
        assert coordinator.get_session("ES") is None
        assert coordinator.list_active_symbols() == ["MNQ"]
        # The other session is untouched and still streaming.
        mnq = coordinator.get_session("MNQ")
        assert mnq is not None
        assert mnq._started is True

    def test_stop_session_unknown_symbol_returns_false(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")

        stopped = coordinator.stop_session("ES")

        assert stopped is False
        assert coordinator.list_active_symbols() == ["MNQ"]

    def test_leave_does_not_stop_with_remaining_clients(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.join_instrument("MNQ", "sid-2")
        coordinator.leave_instrument("MNQ", "sid-1")
        session = coordinator.get_session("MNQ")
        assert session.client_count() == 1
        assert session._started is True


class TestDataRouting:
    def test_route_bar_forwards_to_matching_session(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.join_instrument("ES", "sid-2")

        coordinator.route_bar({"time": 1, "close": 100, "pair": "ES"})
        coordinator.route_bar({"time": 2, "close": 200, "pair": "MNQ"})

        mnq = coordinator.get_session("MNQ")
        es = coordinator.get_session("ES")
        assert len(mnq.bars) == 1
        assert mnq.bars[0]["pair"] == "MNQ"
        assert len(es.bars) == 1
        assert es.bars[0]["pair"] == "ES"

    def test_route_tick_forwards_to_matching_session(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.join_instrument("ES", "sid-2")

        coordinator.route_tick({"time": 1, "price": 100, "pair": "ES"})
        coordinator.route_tick({"time": 2, "price": 200, "pair": "MNQ"})

        mnq = coordinator.get_session("MNQ")
        es = coordinator.get_session("ES")
        assert len(mnq.ticks) == 1
        assert len(es.ticks) == 1

    def test_route_partial_bar_forwards_to_matching_session(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.route_partial_bar({"time": 1, "close": 100, "pair": "MNQ"})
        session = coordinator.get_session("MNQ")
        assert len(session.partial_bars) == 1

    def test_route_history_loaded_routes_by_last_bar_pair(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.join_instrument("ES", "sid-2")

        coordinator.route_history_loaded([
            {"time": 1, "close": 100, "pair": "ES"},
            {"time": 2, "close": 101, "pair": "ES"},
        ])

        mnq = coordinator.get_session("MNQ")
        es = coordinator.get_session("ES")
        assert len(mnq.history_loaded_calls) == 0
        assert len(es.history_loaded_calls) == 1

    def test_route_history_loaded_drops_when_pair_unknown(self, coordinator):
        coordinator.join_instrument("MNQ", "sid-1")
        coordinator.join_instrument("ES", "sid-2")

        coordinator.route_history_loaded([{"time": 1, "close": 100}])

        mnq = coordinator.get_session("MNQ")
        es = coordinator.get_session("ES")
        assert len(mnq.history_loaded_calls) == 0
        assert len(es.history_loaded_calls) == 0

    def test_unknown_pair_creates_session_on_demand(self, coordinator):
        # A request for an unregistered symbol creates a minimal session.
        session = coordinator.get_or_create_session("NQ")
        assert session is not None
        assert session.symbol == "NQ"
        assert len(session.bars) == 0


class TestHelpers:
    def test_require_session_creates_when_missing(self, coordinator):
        session = coordinator.require_session("ES")
        assert session.symbol == "ES"
        assert coordinator.get_session("ES") is session

    def test_require_session_rejects_missing_symbol(self, coordinator):
        with pytest.raises(ValueError):
            coordinator.require_session(None)
        with pytest.raises(ValueError):
            coordinator.require_session("")


class TestStreamActivator:
    @pytest.fixture
    def activator(self):
        return MagicMock()

    @pytest.fixture
    def activated_coordinator(self, registry, factory, activator):
        return StreamCoordinator(
            instrument_registry=registry,
            session_factory=factory,
            stream_activator=activator,
        )

    def test_activator_fires_for_new_symbol(self, activated_coordinator, activator):
        session = activated_coordinator.join_instrument("ES", "sid-1")
        activator.assert_called_once_with(session.instrument)
        assert session.instrument.full_name == "ES 09-26"

    def test_activator_fires_for_every_started_session(self, activated_coordinator, activator):
        activated_coordinator.join_instrument("MNQ", "sid-1")
        activator.assert_called_once()

    def test_activator_not_fired_when_session_already_started(self, activated_coordinator, activator):
        activated_coordinator.join_instrument("ES", "sid-1")
        activated_coordinator.join_instrument("ES", "sid-2")
        activator.assert_called_once()

    def test_activator_exception_does_not_break_joining(self, registry, factory):
        failing = MagicMock(side_effect=RuntimeError("boom"))
        coordinator = StreamCoordinator(
            instrument_registry=registry,
            session_factory=factory,
            stream_activator=failing,
        )
        session = coordinator.join_instrument("ES", "sid-1")
        assert session._started is True
        assert session.client_count() == 1

    def test_no_activator_configured_still_joins(self, coordinator):
        session = coordinator.join_instrument("ES", "sid-1")
        assert session._started is True
