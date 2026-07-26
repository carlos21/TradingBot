"""Session start seeds the readiness monitor with the gateway connection state.

A session created mid-stream (platform already connected) must not sit in
DISCONNECTED — live bars arriving before its first refresh would be silently
dropped and the first refresh_start would force an invalid state transition.
"""

from __future__ import annotations

from app_factory import Repositories
from src.application.streaming_session import StreamingSession
from src.domain.models import Instrument
from src.domain.readiness import ReadinessState
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from tests.fakes import DummySocketIO, FakeLineRepository, FakeLogger, FakeTradeRepository


class _StubGatewayDataSource(ZMQDataSource):
    """ZMQDataSource with a controllable connection state and no real gateway."""

    def __init__(self, logger, connected: bool):
        super().__init__(logger=logger)
        self._stub_connected = connected

    @property
    def is_connected(self) -> bool:
        return self._stub_connected


def _build_live_session(symbol: str, data_source: ZMQDataSource) -> StreamingSession:
    instrument = Instrument(symbol=symbol, full_name=symbol, point_value=2.0)
    numbers = StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=90.0,
        extra_sl_space=0.0,
        fixed_stop_loss=20.0,
        rr_ratio=2.0,
        point_value=2.0,
        account_balance=100000.0,
    )
    repos = Repositories(
        lines=FakeLineRepository(),
        trades=FakeTradeRepository(),
    )
    return StreamingSession(
        instrument=instrument,
        socketio=DummySocketIO(),
        data_source=data_source,
        repos=repos,
        numbers=numbers,
        options=DEFAULT_STRATEGY_OPTIONS,
        candle_config=CandleConfig(),
        timeframes=["5m"],
        live_mode=True,
        logger=FakeLogger(),
        bootstrap_existing_lines=False,
    )


class TestSessionStartConnectionSeeding:
    def test_start_seeds_monitor_when_gateway_connected(self):
        data_source = _StubGatewayDataSource(FakeLogger(), connected=True)
        session = _build_live_session("MNQ", data_source)
        monitor = session.readiness_monitor
        assert monitor is not None
        assert monitor._state_machine.state == ReadinessState.DISCONNECTED

        session.start()

        assert monitor._state_machine.state == ReadinessState.CONNECTED

    def test_start_keeps_disconnected_when_gateway_not_connected(self):
        data_source = _StubGatewayDataSource(FakeLogger(), connected=False)
        session = _build_live_session("MNQ", data_source)
        monitor = session.readiness_monitor

        session.start()

        assert monitor._state_machine.state == ReadinessState.DISCONNECTED

    def test_seeded_monitor_takes_valid_refresh_transition(self):
        """The seeded CONNECTED monitor accepts refresh_start without an
        invalid DISCONNECTED -> REFRESHING transition."""
        data_source = _StubGatewayDataSource(FakeLogger(), connected=True)
        session = _build_live_session("MNQ", data_source)
        session.start()
        monitor = session.readiness_monitor

        monitor.on_refresh_start()

        assert monitor._state_machine.state == ReadinessState.REFRESHING
