"""Tests for src/bars_loader.py."""

from unittest.mock import MagicMock

import pytest

from src.bars_loader import BarsLoader, LoaderConfig
from tests.fakes import FakeDataSource, FakeLogger


@pytest.fixture
def bars_loader():
    ds = FakeDataSource(pair="MNQ")
    socketio = MagicMock()
    logger = FakeLogger()
    return BarsLoader(
        data_source=ds,
        socketio=socketio,
        logger=logger,
        bars_per_second=10.0,
    )


class TestBarsLoaderInit:

    def test_initial_state(self, bars_loader):
        assert bars_loader.data_source.pair == "MNQ"
        assert bars_loader.bars_per_second == 10.0
        assert bars_loader._emit_delay == 0.1
        assert bars_loader.streaming is False
        assert bars_loader.current_tf == "1m"
        assert bars_loader.group_size == 1
        assert bars_loader.live_mode is False
        assert bars_loader._step_mode is False

    def test_invalid_bars_per_second(self):
        ds = FakeDataSource(pair="MNQ")
        loader = BarsLoader(
            data_source=ds,
            socketio=MagicMock(),
            logger=FakeLogger(),
            bars_per_second=-5.0,
        )
        assert loader.bars_per_second == 1.0
        assert loader._emit_delay == 1.0


class TestBarsLoaderReset:

    def test_reset_clears_state(self, bars_loader):
        bars_loader._last_played_ts = 1000
        bars_loader._last_bar_close = 500
        bars_loader._1m_buffer = [{"time": 1}]
        bars_loader.streaming = True
        bars_loader._step_mode = True
        bars_loader._reached_stop_at = True

        bars_loader.reset()

        assert bars_loader._last_played_ts == 0
        assert bars_loader._last_bar_close == 0
        assert bars_loader._1m_buffer == []
        assert bars_loader.streaming is False
        assert bars_loader._step_mode is False
        assert bars_loader._reached_stop_at is False


class TestBarsLoaderSetTimeframe:

    def test_minutes(self, bars_loader):
        bars_loader.set_timeframe("5m")
        assert bars_loader.current_tf == "5m"
        assert bars_loader.group_size == 5

    def test_hours(self, bars_loader):
        bars_loader.set_timeframe("2h")
        assert bars_loader.current_tf == "2h"
        assert bars_loader.group_size == 120

    def test_invalid_unit_raises(self, bars_loader):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            bars_loader.set_timeframe("1d")

    def test_resets_state(self, bars_loader):
        bars_loader._1m_buffer = [{"time": 1}]
        bars_loader.set_timeframe("5m")
        assert bars_loader._1m_buffer == []


class TestBarsLoaderSeek:

    def test_seek(self, bars_loader):
        bars_loader.seek(1000)
        assert bars_loader._from_time == 1000
        assert bars_loader._last_played_ts == 1000

    def test_seek_resets_buffer(self, bars_loader):
        bars_loader._1m_buffer = [{"time": 1}]
        bars_loader.seek(1000)
        assert bars_loader._1m_buffer == []


class TestBarsLoaderPauseResume:

    def test_pause(self, bars_loader):
        bars_loader.streaming = True
        bars_loader.pause()
        assert bars_loader.streaming is False

    def test_start_after_pause(self, bars_loader):
        bars_loader.pause()
        bars_loader.start(from_time=0)
        assert bars_loader.streaming is True


class TestBarsLoaderStart:

    def test_start_with_stop_at(self, bars_loader):
        bars_loader.start(from_time=0, stop_at=5000)
        assert bars_loader._stop_at == 5000
        assert bars_loader._reached_stop_at is False
        assert bars_loader._fast_jump_mode is True
        assert bars_loader._emit_delay == 0.0

    def test_start_without_stop_at(self, bars_loader):
        bars_loader.start(from_time=0)
        assert bars_loader._stop_at is None
        assert bars_loader._fast_jump_mode is False
        assert bars_loader._emit_delay == bars_loader._default_emit_delay


class TestBarsLoaderConfig:

    def test_loader_config_dataclass(self):
        config = LoaderConfig(initial_start=0, initial_end=1000)
        assert config.initial_start == 0
        assert config.initial_end == 1000


class TestBarsLoaderStepMode:

    def test_step_enables_step_mode(self, bars_loader):
        bars_loader.step()
        assert bars_loader._step_mode is True
        assert bars_loader.streaming is True

    def test_pause_disables_step_mode(self, bars_loader):
        bars_loader.step()
        bars_loader.pause()
        assert bars_loader._step_mode is False
        assert bars_loader.streaming is False


class TestBarsLoaderFastJumpMode:

    def test_start_with_stop_at_enables_fast_jump(self, bars_loader):
        bars_loader.start(from_time=0, stop_at=5000)
        assert bars_loader._fast_jump_mode is True
        assert bars_loader._emit_delay == 0.0

    def test_start_without_stop_at_disables_fast_jump(self, bars_loader):
        bars_loader.start(from_time=0, stop_at=5000)
        bars_loader.start(from_time=0)
        assert bars_loader._fast_jump_mode is False
        assert bars_loader._emit_delay == bars_loader._default_emit_delay
