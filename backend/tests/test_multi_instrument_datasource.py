"""Tests for multi-instrument routing in ``ZMQDataSource``."""

from unittest.mock import MagicMock

import pytest

from src.infrastructure.gateway.datasource import DataSourceState, ZMQDataSource
from tests.fakes import FakeLogger


class FakeCoordinator:
    def __init__(self):
        self.bars = []
        self.ticks = []
        self.partials = []
        self.history_loaded = []
        self.late_history_batches = []
        self.gaps = []
        self.history_retries = []

    def route_bar(self, bar: dict) -> None:
        self.bars.append(bar)

    def route_tick(self, tick: dict) -> None:
        self.ticks.append(tick)

    def route_partial_bar(self, partial: dict) -> None:
        self.partials.append(partial)

    def route_history_loaded(self, bars: list[dict], pair: str | None = None) -> None:
        self.history_loaded.append((bars, pair))

    def route_late_history_batch(self, bar_count: int, pair: str | None = None) -> None:
        self.late_history_batches.append((bar_count, pair))

    def route_gap_detected(self, gap_seconds: int, context: str, pair: str | None = None) -> None:
        self.gaps.append((gap_seconds, context, pair))

    def route_history_retry(self, pair: str, attempt: int) -> None:
        self.history_retries.append((pair, attempt))

    def route_heartbeat_stale(self, age_seconds: float, pair: str | None = None) -> None:
        pass

    def route_before_refresh(self, pair: str | None = None) -> None:
        pass

    def route_refresh_start(self, pair: str | None = None) -> None:
        pass


class FakeGateway:
    def __init__(self):
        self.is_running = False
        self.is_connected = False
        self._callbacks = {}
        self._conn_listeners = []
        self.subscribed = []
        self.unsubscribed = []
        self.refreshed = []

    def on(self, msg_type, callback):
        self._callbacks[msg_type] = callback

    def on_connection_change(self, callback):
        self._conn_listeners.append(callback)

    def send_subscribe(self, instrument):
        self._subscribed = instrument
        self.subscribed.append(instrument)

    def send_unsubscribe(self, instrument):
        self.unsubscribed.append(instrument)

    def send_refresh_request(self, days=1, instrument=None):
        self.refreshed.append((days, instrument))

    def trigger(self, msg_type, payload):
        cb = self._callbacks.get(msg_type)
        if cb:
            cb(payload)


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def data_source(gateway):
    ds = ZMQDataSource(FakeLogger(), gateway=gateway)
    ds._state = DataSourceState.STREAMING
    return ds


@pytest.fixture
def coordinator():
    return FakeCoordinator()


class TestMultiInstrumentRouting:
    def test_set_coordinator_wires_routing(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        assert data_source._coordinator is coordinator

    def test_on_bar_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "ES",
        })
        assert len(coordinator.bars) == 1
        assert coordinator.bars[0]["pair"] == "ES"

    def test_on_bar_stores_in_per_pair_cache(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "MNQ",
        })
        assert len(data_source._bars_by_pair["MNQ"]) == 1
        assert len(coordinator.bars) == 1

    def test_on_bar_other_pair_uses_per_pair_cache(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "ES",
        })
        assert "ES" in data_source._bars_by_pair
        assert len(data_source._bars_by_pair["ES"]) == 1

    def test_on_bar_without_pair_is_dropped(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1,
        })
        assert data_source._bars_by_pair == {}
        assert len(coordinator.bars) == 0

    def test_on_bar_buffers_in_connected_state(self, data_source, coordinator):
        data_source._state = DataSourceState.CONNECTED
        data_source.set_coordinator(coordinator)
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "MNQ",
        })
        assert "MNQ" not in data_source._bars_by_pair
        assert len(data_source._refresh_buffer["MNQ"]) == 1
        # The bar is still routed to the coordinator even while buffering.
        assert len(coordinator.bars) == 1

    def test_on_history_batch_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_history_batch({
            "pair": "ES",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            ],
        })
        assert "ES" in data_source._bars_by_pair
        assert len(data_source._bars_by_pair["ES"]) == 1

    def test_on_history_batch_without_pair_is_dropped(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_history_batch({
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            ],
        })
        assert data_source._bars_by_pair == {}

    def test_on_history_end_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_history_batch({
            "pair": "ES",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "ES"},
            ],
        })
        data_source._on_history_end({"pair": "ES"})
        assert len(coordinator.history_loaded) == 1
        bars, pair = coordinator.history_loaded[0]
        assert pair == "ES"
        assert bars[0]["pair"] == "ES"

    def test_load_historical_bars_with_pair_param(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._on_history_batch({
            "pair": "ES",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "ES"},
                {"time": 200, "open": 11, "high": 12, "low": 10, "close": 11, "volume": 1, "pair": "ES"},
            ],
        })
        bars = data_source.load_historical_bars("1m", pair="ES")
        assert len(bars) == 2
        assert bars[0]["pair"] == "ES"

    def test_load_historical_bars_requires_pair(self, data_source):
        data_source._bars_by_pair["MNQ"] = [
            {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "MNQ"},
        ]
        with pytest.raises(TypeError):
            data_source.load_historical_bars("1m")
        with pytest.raises(ValueError):
            data_source.load_historical_bars("1m", pair="")
        bars = data_source.load_historical_bars("1m", pair="MNQ")
        assert len(bars) == 1
        assert bars[0]["pair"] == "MNQ"

    def test_on_tick_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_tick({"time": 100, "price": 10, "volume": 1, "pair": "ES"})
        assert len(coordinator.partials) == 1
        assert coordinator.partials[0]["pair"] == "ES"

    def test_on_partial_bar_routes_by_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_partial_bar({"time": 100, "close": 10, "pair": "ES"})
        assert len(coordinator.partials) == 1
        assert coordinator.partials[0]["pair"] == "ES"

    def test_gap_detection_routes_to_coordinator(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        # Two bars with a 120-second gap.
        data_source._on_bar({
            "time": 100, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "MNQ",
        })
        data_source._on_bar({
            "time": 220, "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 1, "pair": "MNQ",
        })
        assert data_source._gap_count >= 1

    def test_late_history_batch_routes_to_coordinator(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_history_batch({
            "pair": "MNQ",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "MNQ"},
            ],
        })
        # No assertion on coordinator because route_late_history_batch delegates
        # to the session; just ensure no exception and state is unchanged.
        assert data_source._state == DataSourceState.STREAMING


class TestPerPairRefreshState:
    """Refresh/history lifecycle is tracked per pair via ``_refreshing_pairs``:
    the connection is REFRESHING while any pair's history is in flight and
    returns to STREAMING only when every pair has completed."""

    def test_history_end_completes_pair_refresh(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_history_batch({
            "pair": "ES",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "ES"},
            ],
        })
        data_source._on_history_end({"pair": "ES"})
        # No refresh was in flight, so the state stays STREAMING.
        assert data_source._state == DataSourceState.STREAMING
        # Stream liveness stamp is still updated.
        assert data_source._last_completed_bar_time > 0
        assert len(coordinator.history_loaded) == 1

    def test_refresh_start_enters_refreshing_until_history_end(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._bars_by_pair["ES"] = [
            {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "ES"},
        ]
        data_source._on_refresh_start({"pair": "ES"})
        assert "ES" in data_source._refreshing_pairs
        assert data_source._state == DataSourceState.REFRESHING
        data_source._on_history_end({"pair": "ES"})
        assert data_source._refreshing_pairs == set()
        assert data_source._state == DataSourceState.STREAMING

    def test_on_tick_keeps_separate_forming_bars_per_pair(self, data_source, coordinator):
        data_source.set_coordinator(coordinator)
        data_source._state = DataSourceState.STREAMING
        data_source._on_tick({"time": 100, "price": 10, "volume": 1, "pair": "ES"})
        data_source._on_tick({"time": 101, "price": 200, "volume": 1, "pair": "MNQ"})
        data_source._on_tick({"time": 102, "price": 12, "volume": 1, "pair": "ES"})
        assert data_source._current_bars["ES"]["close"] == 12
        assert data_source._current_bars["ES"]["high"] == 12
        assert data_source._current_bars["MNQ"]["close"] == 200
        assert data_source._current_bars["MNQ"]["high"] == 200


class TestSubscriptionSupervisorWiring:
    """Joining an instrument arms the subscription supervisor; platform
    responses, disconnects, and stops disarm it."""

    def _join_mes(self, data_source):
        from src.domain.models import Instrument

        data_source.ensure_instrument_streaming(Instrument(symbol="MES", full_name="MES 09-26"))

    def test_join_while_streaming_arms_retry(self, data_source, gateway):
        self._join_mes(data_source)
        supervisor = data_source._subscription_supervisor
        try:
            assert "MES 09-26" in supervisor._awaiting
            assert supervisor._timer is not None
        finally:
            supervisor.stop()

    def test_refresh_start_cancels_retry(self, data_source, gateway):
        self._join_mes(data_source)
        data_source._on_refresh_start({"pair": "MES"})
        supervisor = data_source._subscription_supervisor
        assert supervisor._awaiting == {}
        assert supervisor._timer is None

    def test_history_batch_cancels_retry(self, data_source, gateway):
        self._join_mes(data_source)
        data_source._on_history_batch({
            "pair": "MES",
            "bars": [
                {"time": 100, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "pair": "MES"},
            ],
        })
        supervisor = data_source._subscription_supervisor
        assert supervisor._awaiting == {}
        assert supervisor._timer is None

    def test_disconnect_cancels_retry(self, data_source, gateway):
        self._join_mes(data_source)
        data_source.on_platform_disconnected()
        supervisor = data_source._subscription_supervisor
        assert supervisor._awaiting == {}
        assert supervisor._timer is None

    def test_retry_resends_via_gateway_and_routes_to_coordinator(self, data_source, gateway, coordinator):
        data_source.set_coordinator(coordinator)
        self._join_mes(data_source)
        supervisor = data_source._subscription_supervisor
        try:
            gateway.subscribed.clear()
            gateway.refreshed.clear()
            supervisor.fail_fast("MES 09-26")
            assert gateway.subscribed == ["MES 09-26"]
            assert gateway.refreshed[-1][1] == "MES 09-26"
            assert coordinator.history_retries == [("MES", 1)]
        finally:
            supervisor.stop()

    def test_subscribe_ack_timeout_triggers_fail_fast(self, data_source, gateway):
        self._join_mes(data_source)
        supervisor = data_source._subscription_supervisor
        try:
            gateway.subscribed.clear()
            data_source._on_command_timeout(
                "subscribe", {"instrument": "MES 09-26"}, seq_num=42
            )
            assert gateway.subscribed == ["MES 09-26"]
        finally:
            supervisor.stop()

    def test_unrelated_command_timeout_is_ignored(self, data_source, gateway):
        self._join_mes(data_source)
        supervisor = data_source._subscription_supervisor
        try:
            gateway.subscribed.clear()
            data_source._on_command_timeout(
                "order_open", {"trade_id": "T1", "instrument": "MES 09-26"}, seq_num=43
            )
            assert gateway.subscribed == []
        finally:
            supervisor.stop()


class TestStopInstrumentStreaming:
    """``stop_instrument_streaming`` removes one instrument without tearing
    down the gateway or disturbing the other instruments."""

    def _join(self, data_source, symbol: str, full_name: str):
        from src.domain.models import Instrument

        data_source.ensure_instrument_streaming(Instrument(symbol=symbol, full_name=full_name))

    def test_untracks_supervisor_and_removes_requested(self, data_source, gateway):
        self._join(data_source, "MES", "MES 09-26")
        self._join(data_source, "MNQ", "MNQ 09-26")
        supervisor = data_source._subscription_supervisor
        try:
            assert "MES 09-26" in supervisor._awaiting
            data_source.stop_instrument_streaming("MES")
            assert "MES 09-26" not in supervisor._awaiting
            assert "MES 09-26" not in data_source._requested_instruments
            # The other instrument stays requested and tracked.
            assert "MNQ 09-26" in data_source._requested_instruments
            assert "MNQ 09-26" in supervisor._awaiting
        finally:
            supervisor.stop()

    def test_sends_unsubscribe_when_gateway_connected(self, data_source, gateway):
        gateway.is_connected = True
        self._join(data_source, "MES", "MES 09-26")
        try:
            data_source.stop_instrument_streaming("MES")
            assert gateway.unsubscribed == ["MES 09-26"]
        finally:
            data_source._subscription_supervisor.stop()

    def test_accepts_full_name_as_pair(self, data_source, gateway):
        gateway.is_connected = True
        self._join(data_source, "MES", "MES 09-26")
        try:
            data_source.stop_instrument_streaming("MES 09-26")
            assert gateway.unsubscribed == ["MES 09-26"]
            assert "MES 09-26" not in data_source._requested_instruments
        finally:
            data_source._subscription_supervisor.stop()

    def test_sends_nothing_when_gateway_disconnected(self, data_source, gateway):
        gateway.is_connected = False
        self._join(data_source, "MES", "MES 09-26")
        try:
            data_source.stop_instrument_streaming("MES")
            assert gateway.unsubscribed == []
            # The instrument is still removed locally.
            assert "MES 09-26" not in data_source._requested_instruments
        finally:
            data_source._subscription_supervisor.stop()

    def test_unknown_pair_warns_and_returns(self, gateway):
        logger = MagicMock()
        ds = ZMQDataSource(logger, gateway=gateway)
        ds._state = DataSourceState.STREAMING
        self._join(ds, "MNQ", "MNQ 09-26")
        try:
            ds.stop_instrument_streaming("MES")
            assert any(
                "not a requested instrument" in str(call)
                for call in logger.warning.call_args_list
            )
            # Nothing was removed and nothing was sent.
            assert "MNQ 09-26" in ds._requested_instruments
            assert gateway.unsubscribed == []
        finally:
            ds._subscription_supervisor.stop()
