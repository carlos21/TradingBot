"""Expanded tests for src/bars_loader.py covering previously untested paths."""

from unittest.mock import MagicMock, patch

import pytest

from src.bars_loader import BarsLoader, LoaderConfig
from tests.fakes import DummySocketIO, FakeDataSource, FakeLogger


@pytest.fixture
def fake_ds():
    return FakeDataSource(pair="MNQ")


@pytest.fixture
def socketio():
    return DummySocketIO()


@pytest.fixture
def logger():
    return FakeLogger()


@pytest.fixture
def bars_loader(fake_ds, socketio, logger):
    return BarsLoader(
        data_source=fake_ds,
        socketio=socketio,
        logger=logger,
        bars_per_second=10.0,
    )


class TestHandleMessageEnd:
    """Tests for _handle_message with _end messages."""

    def test_end_message_sets_streaming_false(self, bars_loader):
        bars_loader.streaming = True
        bars_loader._handle_message({"_end": True})
        assert bars_loader.streaming is False

    def test_end_message_calls_stream_end_callback(self, bars_loader):
        cb = MagicMock()
        bars_loader.stream_end_callback = cb
        bars_loader._last_played_ts = 1000
        bars_loader._last_bar_close = 50.0
        bars_loader._handle_message({"_end": True})
        cb.assert_called_once_with(50.0, 1000)

    def test_end_message_does_not_call_callback_if_last_played_ts_zero(self, bars_loader):
        cb = MagicMock()
        bars_loader.stream_end_callback = cb
        bars_loader._last_played_ts = 0
        bars_loader._handle_message({"_end": True})
        cb.assert_not_called()

    def test_end_message_emits_stream_status_and_stream_end(self, bars_loader, socketio):
        bars_loader._last_played_ts = 100
        bars_loader._handle_message({"_end": True})
        events = [e for e in socketio.events if e[0] in ("stream_status", "stream_end")]
        assert ("stream_status", {"playing": False}) in events
        assert ("stream_end", {"ok": True}) in events


class TestHandleMessagePartialBars:
    """Tests for _handle_message with partial bars."""

    def test_partial_bar_1m_emits_without_partial_key(self, bars_loader, socketio):
        bars_loader.current_tf = "1m"
        msg = {"time": 1000, "open": 10, "high": 15, "low": 9, "close": 12, "partial": True}
        bars_loader._handle_message(msg)
        emitted = [e for e in socketio.events if e[0] == "bar"]
        assert len(emitted) == 1
        assert "partial" not in emitted[0][1]
        assert emitted[0][1]["time"] == 1000

    def test_partial_bar_updates_last_played_ts_and_close(self, bars_loader):
        bars_loader.current_tf = "1m"
        msg = {"time": 2000, "close": 99.5, "partial": True}
        bars_loader._handle_message(msg)
        assert bars_loader._last_played_ts == 2000
        assert bars_loader._last_bar_close == 99.5

    def test_partial_bar_higher_tf_uses_window_start(self, bars_loader, socketio):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        # window for 5m: 0-299
        msg = {"time": 120, "open": 10, "high": 15, "low": 9, "close": 12, "partial": True}
        bars_loader._handle_message(msg)
        emitted = [e for e in socketio.events if e[0] == "bar"]
        assert emitted[0][1]["time"] == 0

    def test_partial_bar_higher_tf_merges_with_buffer(self, bars_loader, socketio):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._1m_buffer = [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
        ]
        msg = {"time": 120, "open": 11, "high": 16, "low": 10, "close": 14, "volume": 50, "partial": True}
        bars_loader._handle_message(msg)
        emitted = [e for e in socketio.events if e[0] == "bar"]
        bar = emitted[0][1]
        assert bar["open"] == 10
        assert bar["high"] == 16
        assert bar["low"] == 9
        assert bar["close"] == 14
        assert bar["volume"] == 150

    def test_partial_bar_no_buffer_uses_partial_as_is(self, bars_loader, socketio):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._1m_buffer = []
        msg = {"time": 120, "open": 10, "high": 15, "low": 9, "close": 12, "volume": 50, "partial": True}
        bars_loader._handle_message(msg)
        emitted = [e for e in socketio.events if e[0] == "bar"]
        bar = emitted[0][1]
        assert bar["open"] == 10
        assert bar["close"] == 12


class TestHandleMessageStopEvent:
    """Tests for _handle_message stop_event behavior in non-live mode."""

    def test_message_ignored_when_stop_event_set_and_not_live(self, bars_loader):
        bars_loader.live_mode = False
        bars_loader._stop_event.set()
        socketio_before = list(bars_loader.socketio.events)
        bars_loader._handle_message({"time": 100, "open": 10, "high": 10, "low": 10, "close": 10})
        assert bars_loader.socketio.events == socketio_before

    def test_message_processed_when_stop_event_set_but_live(self, bars_loader, socketio):
        bars_loader.live_mode = True
        bars_loader._stop_event.set()
        bars_loader._handle_message({"time": 100, "open": 10, "high": 10, "low": 10, "close": 10})
        assert any(e[0] == "bar" for e in socketio.events)


class TestHandleMessageBarCallback:
    """Tests for bar_callback invocation."""

    def test_bar_callback_called_for_full_bar(self, bars_loader):
        cb = MagicMock()
        bars_loader.bar_callback = cb
        bars_loader._handle_message({"time": 100, "open": 10, "high": 12, "low": 9, "close": 11})
        cb.assert_called_once()

    def test_bar_callback_not_called_for_partial(self, bars_loader):
        cb = MagicMock()
        bars_loader.bar_callback = cb
        bars_loader._handle_message({"time": 100, "open": 10, "high": 12, "low": 9, "close": 11, "partial": True})
        cb.assert_not_called()

    def test_bar_callback_called_for_tick(self, bars_loader):
        cb = MagicMock()
        bars_loader.bar_callback = cb
        bars_loader._handle_message({"time": 100, "price": 10.5})
        cb.assert_called_once_with({"time": 100, "price": 10.5})


class TestHandleMessageStopAt:
    """Tests for stop_at behavior in _handle_message."""

    def test_stop_at_reached_sets_flags(self, bars_loader):
        bars_loader._stop_at = 500
        bars_loader.streaming = True
        bars_loader._handle_message({"time": 500, "open": 10, "high": 10, "low": 10, "close": 10})
        assert bars_loader._reached_stop_at is True
        assert bars_loader.streaming is False
        assert bars_loader._stop_event.is_set()

    def test_stop_at_reached_calls_stream_end_callback(self, bars_loader):
        cb = MagicMock()
        bars_loader.stream_end_callback = cb
        bars_loader._stop_at = 500
        bars_loader.streaming = True
        bars_loader._handle_message({"time": 500, "open": 10, "high": 10, "low": 10, "close": 99.0})
        cb.assert_called_once_with(99.0, 500)

    def test_stop_at_reached_uses_last_bar_close_if_no_close(self, bars_loader):
        cb = MagicMock()
        bars_loader.stream_end_callback = cb
        bars_loader._stop_at = 500
        bars_loader._last_bar_close = 88.0
        bars_loader.streaming = True
        # Use a tick message (no open/high) so _process_bar doesn't overwrite _last_bar_close
        bars_loader._handle_message({"time": 500, "price": 10.5})
        cb.assert_called_once_with(88.0, 500)

    def test_stop_at_reached_emits_events(self, bars_loader, socketio):
        bars_loader._stop_at = 500
        bars_loader.streaming = True
        bars_loader._handle_message({"time": 500, "open": 10, "high": 10, "low": 10, "close": 10})
        assert ("stream_status", {"playing": False}) in socketio.events
        assert ("stream_end", {"reason": "day_end", "stop_at": 500}) in socketio.events

    def test_stop_at_not_reached_if_time_below(self, bars_loader):
        bars_loader._stop_at = 500
        bars_loader.streaming = True
        bars_loader._handle_message({"time": 499, "open": 10, "high": 10, "low": 10, "close": 10})
        assert bars_loader._reached_stop_at is False
        assert bars_loader.streaming is True


class TestHandleMessageTicks:
    """Tests for tick messages in _handle_message."""

    def test_tick_emits_tick_event(self, bars_loader, socketio):
        bars_loader._handle_message({"time": 100, "price": 10.5})
        assert ("tick", {"time": 100, "price": 10.5}) in socketio.events

    def test_tick_updates_last_played_ts(self, bars_loader):
        bars_loader._handle_message({"time": 200, "price": 11.0})
        assert bars_loader._last_played_ts == 200


class TestProcessBar:
    """Tests for _process_bar method."""

    def test_1m_emits_bar_directly(self, bars_loader, socketio):
        bars_loader.current_tf = "1m"
        bar = {"time": 60, "open": 10, "high": 12, "low": 9, "close": 11}
        with patch("src.bars_loader.time.sleep"):
            bars_loader._process_bar(bar)
        assert ("bar", bar) in socketio.events

    def test_1m_step_mode_stops_after_emit(self, bars_loader):
        bars_loader.current_tf = "1m"
        bars_loader._step_mode = True
        bars_loader.streaming = True
        bar = {"time": 60, "open": 10, "high": 12, "low": 9, "close": 11}
        with patch("src.bars_loader.time.sleep"):
            bars_loader._process_bar(bar)
        assert bars_loader.streaming is False
        assert bars_loader._step_mode is False

    def test_higher_tf_adds_to_buffer_same_window(self, bars_loader):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bar = {"time": 60, "open": 10, "high": 12, "low": 9, "close": 11}
        with patch("src.bars_loader.time.sleep"):
            bars_loader._process_bar(bar)
        assert bars_loader._1m_buffer == [bar]
        assert bars_loader._current_group_start == 0

    def test_higher_tf_aggregates_on_window_change(self, bars_loader, socketio):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._current_group_start = 0
        bars_loader._1m_buffer = [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
        ]
        bar = {"time": 300, "open": 11, "high": 13, "low": 10, "close": 12, "volume": 50, "pair": "MNQ"}
        with patch("src.bars_loader.time.sleep"):
            bars_loader._process_bar(bar)
        emitted = [e for e in socketio.events if e[0] == "bar"]
        assert len(emitted) == 1
        agg = emitted[0][1]
        assert agg["time"] == 0
        assert agg["open"] == 10
        assert agg["high"] == 12
        assert agg["low"] == 9
        assert agg["close"] == 11
        assert agg["volume"] == 100
        assert bars_loader._1m_buffer == [bar]
        assert bars_loader._current_group_start == 300

    def test_higher_tf_duplicate_time_ignored(self, bars_loader):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._current_group_start = 0
        existing = {"time": 60, "open": 10, "high": 12, "low": 9, "close": 11}
        bars_loader._1m_buffer = [existing]
        duplicate = {"time": 60, "open": 11, "high": 13, "low": 10, "close": 12}
        with patch("src.bars_loader.time.sleep"):
            bars_loader._process_bar(duplicate)
        assert bars_loader._1m_buffer == [existing]

    def test_higher_tf_step_mode_stops_after_emit(self, bars_loader):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._step_mode = True
        bars_loader.streaming = True
        bars_loader._current_group_start = 0
        bars_loader._1m_buffer = [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
        ]
        bar = {"time": 300, "open": 11, "high": 13, "low": 10, "close": 12, "volume": 50, "pair": "MNQ"}
        with patch("src.bars_loader.time.sleep"):
            bars_loader._process_bar(bar)
        assert bars_loader.streaming is False
        assert bars_loader._step_mode is False

    def test_live_mode_gap_detection(self, bars_loader, socketio, logger):
        bars_loader.live_mode = True
        bars_loader._last_processed_bar_time = 100
        bar = {"time": 400, "open": 10, "high": 12, "low": 9, "close": 11}
        with patch("src.bars_loader.time.sleep"):
            bars_loader._process_bar(bar)
        # gap is 300s which is > 180 threshold
        assert bars_loader._last_processed_bar_time == 400

    def test_live_mode_no_gap_when_small(self, bars_loader):
        bars_loader.live_mode = True
        bars_loader._last_processed_bar_time = 100
        bar = {"time": 200, "open": 10, "high": 12, "low": 9, "close": 11}
        with patch("src.bars_loader.time.sleep"):
            bars_loader._process_bar(bar)
        assert bars_loader._last_processed_bar_time == 200

    def test_higher_tf_no_emit_if_buffer_empty_on_window_change(self, bars_loader, socketio):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._current_group_start = 0
        bars_loader._1m_buffer = []
        bar = {"time": 300, "open": 11, "high": 13, "low": 10, "close": 12, "volume": 50, "pair": "MNQ"}
        with patch("src.bars_loader.time.sleep"):
            bars_loader._process_bar(bar)
        assert not any(e[0] == "bar" for e in socketio.events)
        assert bars_loader._1m_buffer == [bar]


class TestProcessTick:
    """Tests for _process_tick method."""

    def test_process_tick_emits(self, bars_loader, socketio):
        tick = {"time": 100, "price": 10.5}
        bars_loader._process_tick(tick)
        assert ("tick", tick) in socketio.events


class TestAggregateTimeWindow:
    """Tests for _aggregate_time_window static method."""

    def test_aggregation(self):
        bars = [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
            {"time": 60, "open": 11, "high": 15, "low": 10, "close": 13, "volume": 200, "pair": "MNQ"},
        ]
        result = BarsLoader._aggregate_time_window(bars, 0, 300)
        assert result["time"] == 0
        assert result["open"] == 10
        assert result["high"] == 15
        assert result["low"] == 9
        assert result["close"] == 13
        assert result["volume"] == 300
        assert result["pair"] == "MNQ"


class TestRunSubscription:
    """Tests for _run_subscription method."""

    def test_subscribes_and_processes_bars(self, bars_loader, fake_ds, socketio):
        fake_ds.set_bars([
            {"time": 60, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
            {"time": 120, "open": 11, "high": 13, "low": 10, "close": 12, "volume": 100, "pair": "MNQ"},
        ])
        with patch("src.bars_loader.time.sleep"):
            bars_loader._run_subscription(0)
        assert any(e[0] == "bar" for e in socketio.events)

    def test_flushes_on_eof_for_higher_tf(self, bars_loader, fake_ds, socketio):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._current_group_start = 0
        bars_loader._1m_buffer = [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
        ]
        fake_ds.set_bars([])
        with patch("src.bars_loader.time.sleep"):
            bars_loader._run_subscription(0)
        assert ("bar", {
            "time": 0, "open": 10, "high": 12, "low": 9, "close": 11,
            "volume": 100, "pair": "MNQ"
        }) in socketio.events

    def test_no_flush_on_pause(self, bars_loader, fake_ds, socketio):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._current_group_start = 0
        bars_loader._1m_buffer = [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
        ]
        bars_loader._stop_event.set()
        fake_ds.set_bars([])
        with patch("src.bars_loader.time.sleep"):
            bars_loader._run_subscription(0)
        # because stop_event is set and _reached_stop_at is False, should_flush is False
        assert not any(e[0] == "bar" for e in socketio.events)
        assert ("stream_end", {"reason": "paused"}) in socketio.events

    def test_flush_on_reached_stop_at(self, bars_loader, fake_ds, socketio):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._current_group_start = 0
        bars_loader._1m_buffer = [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
        ]
        bars_loader._reached_stop_at = True
        bars_loader._stop_event.set()
        fake_ds.set_bars([])
        with patch("src.bars_loader.time.sleep"):
            bars_loader._run_subscription(0)
        assert any(e[0] == "bar" for e in socketio.events)
        assert ("stream_end", {"reason": "eof"}) in socketio.events

    def test_fast_jump_mode_reset_after_run(self, bars_loader, fake_ds):
        bars_loader._fast_jump_mode = True
        bars_loader._emit_delay = 0.0
        fake_ds.set_bars([])
        with patch("src.bars_loader.time.sleep"):
            bars_loader._run_subscription(0)
        assert bars_loader._fast_jump_mode is False
        assert bars_loader._emit_delay == bars_loader._default_emit_delay

    def test_no_flush_for_1m_tf(self, bars_loader, fake_ds, socketio):
        bars_loader.current_tf = "1m"
        bars_loader._1m_buffer = [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
        ]
        fake_ds.set_bars([])
        with patch("src.bars_loader.time.sleep"):
            bars_loader._run_subscription(0)
        # 1m tf doesn't flush _1m_buffer on eof
        bar_emits = [e for e in socketio.events if e[0] == "bar"]
        assert len(bar_emits) == 0

    def test_exception_in_flush_is_suppressed(self, bars_loader, fake_ds, socketio):
        bars_loader.current_tf = "5m"
        bars_loader.group_size = 5
        bars_loader._current_group_start = 0
        bars_loader._1m_buffer = [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "pair": "MNQ"},
        ]
        fake_ds.set_bars([])

        def raise_on_bar(event, payload):
            if event == "bar":
                raise Exception("boom")

        with patch("src.bars_loader.time.sleep"):
            with patch.object(socketio, "emit", side_effect=raise_on_bar):
                bars_loader._run_subscription(0)
        # should not raise
        assert bars_loader.streaming is False

    def test_sets_streaming_false_on_eof(self, bars_loader, fake_ds):
        bars_loader.streaming = True
        fake_ds.set_bars([])
        with patch("src.bars_loader.time.sleep"):
            bars_loader._run_subscription(0)
        assert bars_loader.streaming is False


class TestGetSourceBarsAndAllBars:
    """Tests for _get_source_bars and _get_all_bars."""

    def test_get_source_bars_returns_1m_bars(self, bars_loader, fake_ds):
        fake_ds.set_bars([{"time": 0}])
        assert bars_loader._get_source_bars() == [{"time": 0}]

    def test_get_all_bars_returns_bars(self, bars_loader, fake_ds):
        fake_ds.set_bars([{"time": 0}])
        assert bars_loader._get_all_bars() == [{"time": 0}]

    def test_get_all_bars_returns_none_if_empty(self, bars_loader, fake_ds):
        fake_ds.set_bars([])
        assert bars_loader._get_all_bars() is None


class TestFindNextSameTimeNextDay:
    """Tests for _find_next_same_time_next_day."""

    def test_forward_finds_next_day(self, bars_loader, fake_ds):
        fake_ds.set_bars([
            {"time": 0},
            {"time": 86400},
            {"time": 172800},
        ])
        result = bars_loader._find_next_same_time_next_day(0, 1)
        assert result == 86400

    def test_forward_returns_last_if_no_match(self, bars_loader, fake_ds):
        fake_ds.set_bars([
            {"time": 0},
            {"time": 100},
        ])
        result = bars_loader._find_next_same_time_next_day(0, 1)
        assert result == 100

    def test_backward_finds_prev_day(self, bars_loader, fake_ds):
        fake_ds.set_bars([
            {"time": 0},
            {"time": 86400},
            {"time": 172800},
        ])
        result = bars_loader._find_next_same_time_next_day(172800, -1)
        assert result == 86400

    def test_backward_returns_first_if_no_match(self, bars_loader, fake_ds):
        fake_ds.set_bars([
            {"time": 100},
            {"time": 200},
        ])
        result = bars_loader._find_next_same_time_next_day(200, -1)
        assert result == 100

    def test_no_bars_returns_approximate(self, bars_loader):
        result = bars_loader._find_next_same_time_next_day(0, 1)
        assert result == 86400


class TestJumpDay:
    """Tests for jump_day method."""

    def test_jump_forward_fast(self, bars_loader, fake_ds):
        fake_ds.set_bars([
            {"time": 0},
            {"time": 86400},
        ])
        # Mock start_background_task so _run_subscription doesn't execute synchronously
        with patch.object(bars_loader.socketio, "start_background_task") as mock_task:
            with patch("src.bars_loader.time.sleep"):
                result = bars_loader.jump_day(1, fast=True)
        assert result == 86400
        # Note: start() unconditionally resets _fast_jump_mode to False when stop_at is None,
        # so jump_day(fast=True) ends up with _fast_jump_mode=False after start() returns.
        assert bars_loader._fast_jump_mode is False
        mock_task.assert_called_once()

    def test_jump_forward_slow(self, bars_loader, fake_ds):
        fake_ds.set_bars([
            {"time": 0},
            {"time": 86400},
        ])
        with patch("src.bars_loader.time.sleep"):
            result = bars_loader.jump_day(1, fast=False)
        assert result == 86400
        assert bars_loader._fast_jump_mode is False
        assert bars_loader._emit_delay == bars_loader._default_emit_delay

    def test_jump_backward(self, bars_loader, fake_ds):
        fake_ds.set_bars([
            {"time": 0},
            {"time": 86400},
        ])
        with patch("src.bars_loader.time.sleep"):
            result = bars_loader.jump_day(-1, fast=True)
        assert result == 0

    def test_jump_day_live_mode_returns_current(self, bars_loader):
        bars_loader.live_mode = True
        bars_loader._last_played_ts = 5000
        result = bars_loader.jump_day(1, fast=True)
        assert result == 5000

    def test_jump_day_uses_last_played_ts_if_no_bars(self, bars_loader):
        bars_loader._last_played_ts = 1000
        with patch("src.bars_loader.time.sleep"):
            result = bars_loader.jump_day(1, fast=True)
        # no bars, so uses last_played_ts, adds 86400 -> 87400, but no bars to match
        # returns approximate
        assert result == 87400

    def test_jump_day_uses_from_time_if_no_bars_or_last_played(self, bars_loader):
        bars_loader._from_time = 2000
        with patch("src.bars_loader.time.sleep"):
            result = bars_loader.jump_day(1, fast=True)
        assert result == 2000 + 86400


class TestLiveModeBehavior:
    """Tests for live_mode specific behaviors."""

    def test_set_timeframe_does_not_set_stop_event_in_live_mode(self, bars_loader):
        bars_loader.live_mode = True
        bars_loader._stop_event.clear()
        bars_loader.set_timeframe("5m")
        assert not bars_loader._stop_event.is_set()

    def test_pause_does_not_call_data_source_pause_in_live_mode(self, bars_loader, fake_ds):
        bars_loader.live_mode = True
        bars_loader.pause()
        assert fake_ds._paused is False

    def test_pause_calls_data_source_pause_in_non_live_mode(self, bars_loader, fake_ds):
        bars_loader.live_mode = False
        bars_loader.pause()
        assert fake_ds._paused is True

    def test_handle_message_ignores_stop_event_in_live_mode(self, bars_loader, socketio):
        bars_loader.live_mode = True
        bars_loader._stop_event.set()
        bars_loader._handle_message({"time": 100, "open": 10, "high": 10, "low": 10, "close": 10})
        assert any(e[0] == "bar" for e in socketio.events)


class TestStartMethod:
    """Expanded tests for start() method."""

    def test_start_with_none_from_time_uses_existing(self, bars_loader):
        bars_loader._from_time = 500
        bars_loader.start(from_time=None)
        assert bars_loader._from_time == 500

    def test_start_clears_buffer_and_group_start(self, bars_loader):
        bars_loader._1m_buffer = [{"time": 1}]
        bars_loader._current_group_start = 0
        bars_loader.start(from_time=100)
        assert bars_loader._1m_buffer == []
        assert bars_loader._current_group_start is None

    def test_start_sets_streaming_and_clears_stop_event(self, bars_loader):
        bars_loader._stop_event.set()
        bars_loader.streaming = False
        with patch.object(bars_loader.socketio, "start_background_task"):
            bars_loader.start(from_time=0)
        assert bars_loader.streaming is True
        assert not bars_loader._stop_event.is_set()

    def test_start_spawns_background_task(self, bars_loader, socketio):
        with patch.object(socketio, "start_background_task") as mock_task:
            bars_loader.start(from_time=100)
            mock_task.assert_called_once()

    def test_start_with_stop_at_sets_fast_jump(self, bars_loader):
        with patch.object(bars_loader.socketio, "start_background_task"):
            bars_loader.start(from_time=0, stop_at=500)
        assert bars_loader._fast_jump_mode is True
        assert bars_loader._emit_delay == 0.0


class TestPauseMethod:
    """Expanded tests for pause() method."""

    def test_pause_sets_stop_event_in_non_live(self, bars_loader):
        bars_loader._stop_event.clear()
        bars_loader.pause()
        assert bars_loader._stop_event.is_set()

    def test_pause_clears_stop_at(self, bars_loader):
        bars_loader._stop_at = 500
        bars_loader.pause()
        assert bars_loader._stop_at is None

    def test_pause_clears_step_mode(self, bars_loader):
        bars_loader._step_mode = True
        bars_loader.pause()
        assert bars_loader._step_mode is False

    def test_pause_sets_streaming_false(self, bars_loader):
        bars_loader.streaming = True
        bars_loader.pause()
        assert bars_loader.streaming is False

    def test_pause_suppresses_data_source_pause_exception(self, bars_loader, fake_ds):
        fake_ds.pause = MagicMock(side_effect=Exception("pause failed"))
        bars_loader.pause()
        assert bars_loader.streaming is False


class TestStepMethod:
    """Expanded tests for step() method."""

    def test_step_sets_step_mode(self, bars_loader):
        bars_loader.step()
        assert bars_loader._step_mode is True

    def test_step_clears_stop_at(self, bars_loader):
        bars_loader._stop_at = 500
        bars_loader.step()
        assert bars_loader._stop_at is None

    def test_step_disables_fast_jump(self, bars_loader):
        bars_loader._fast_jump_mode = True
        bars_loader.step()
        assert bars_loader._fast_jump_mode is False

    def test_step_sets_emit_delay_to_default(self, bars_loader):
        bars_loader._emit_delay = 0.0
        bars_loader.step()
        assert bars_loader._emit_delay == bars_loader._default_emit_delay

    def test_step_spawns_background_task(self, bars_loader, socketio):
        with patch.object(socketio, "start_background_task") as mock_task:
            bars_loader.step()
            mock_task.assert_called_once()


class TestSetTimeframe:
    """Expanded tests for set_timeframe."""

    def test_set_timeframe_in_live_mode(self, bars_loader):
        bars_loader.live_mode = True
        bars_loader._stop_event.clear()
        bars_loader.set_timeframe("5m")
        assert bars_loader.current_tf == "5m"
        assert not bars_loader._stop_event.is_set()

    def test_set_timeframe_loads_buffer_from_window(self, bars_loader, fake_ds):
        fake_ds.set_bars([
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11},
            {"time": 60, "open": 11, "high": 13, "low": 10, "close": 12},
            {"time": 120, "open": 12, "high": 14, "low": 11, "close": 13},
        ])
        bars_loader._from_time = 120
        bars_loader.set_timeframe("5m")
        # window_start for 120 in 5m (300s window) is 0
        assert bars_loader._1m_buffer == [
            {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11},
            {"time": 60, "open": 11, "high": 13, "low": 10, "close": 12},
            {"time": 120, "open": 12, "high": 14, "low": 11, "close": 13},
        ]
        assert bars_loader._current_group_start == 0

    def test_set_timeframe_resets_reached_stop_at(self, bars_loader):
        bars_loader._reached_stop_at = True
        bars_loader.set_timeframe("5m")
        assert bars_loader._reached_stop_at is False


class TestSeek:
    """Expanded tests for seek."""

    def test_seek_resets_last_processed_bar_time(self, bars_loader):
        bars_loader._last_processed_bar_time = 1000
        bars_loader.seek(500)
        assert bars_loader._last_processed_bar_time == 0


class TestReset:
    """Expanded tests for reset."""

    def test_reset_clears_last_processed_bar_time(self, bars_loader):
        bars_loader._last_processed_bar_time = 1000
        bars_loader.reset()
        assert bars_loader._last_processed_bar_time == 0

    def test_reset_clears_from_time(self, bars_loader):
        bars_loader._from_time = 500
        bars_loader.reset()
        assert bars_loader._from_time == 0


class TestLoaderConfig:
    """Expanded tests for LoaderConfig."""

    def test_loader_config_defaults(self):
        config = LoaderConfig(initial_start=0, initial_end=100)
        assert config.initial_start == 0
        assert config.initial_end == 100


class TestEdgeCases:
    """Edge case tests."""

    def test_invalid_message_ignored(self, bars_loader, socketio):
        # message without 'open', 'high', or 'price'
        bars_loader._handle_message({"time": 100, "foo": "bar"})
        assert not any(e[0] in ("bar", "tick") for e in socketio.events)

    def test_stop_at_with_tick_message(self, bars_loader):
        bars_loader._stop_at = 500
        bars_loader.streaming = True
        bars_loader._handle_message({"time": 500, "price": 10.5})
        assert bars_loader._reached_stop_at is True
        assert bars_loader.streaming is False

    def test_empty_dict_message(self, bars_loader, socketio):
        bars_loader._handle_message({})
        # should not crash, no emits
        assert not any(e[0] in ("bar", "tick", "stream_status", "stream_end") for e in socketio.events)

    def test_handle_message_not_dict(self, bars_loader, socketio):
        # _handle_message expects dict; passing non-dict would crash on msg.get
        # but we test the partial path guard
        with pytest.raises(AttributeError):
            bars_loader._handle_message("not a dict")

    def test_start_stop_start_cycle(self, bars_loader):
        with patch.object(bars_loader.socketio, "start_background_task"):
            bars_loader.start(from_time=0)
        assert bars_loader.streaming is True
        bars_loader.pause()
        assert bars_loader.streaming is False
        with patch.object(bars_loader.socketio, "start_background_task"):
            bars_loader.start(from_time=100)
        assert bars_loader.streaming is True
        assert bars_loader._from_time == 100

    def test_fast_jump_then_normal_start(self, bars_loader):
        with patch.object(bars_loader.socketio, "start_background_task"):
            bars_loader.start(from_time=0, stop_at=500)
        assert bars_loader._fast_jump_mode is True
        with patch.object(bars_loader.socketio, "start_background_task"):
            bars_loader.start(from_time=0)
        assert bars_loader._fast_jump_mode is False

    def test_set_timeframe_zero_minutes(self, bars_loader):
        bars_loader.set_timeframe("0m")
        assert bars_loader.group_size == 1

    def test_set_timeframe_zero_hours(self, bars_loader):
        bars_loader.set_timeframe("0h")
        assert bars_loader.group_size == 1
