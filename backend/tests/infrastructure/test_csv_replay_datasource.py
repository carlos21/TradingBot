"""Tests for src/infrastructure/data_sources/csv_replay_datasource.py."""

from datetime import datetime, timezone
from threading import Event
from unittest.mock import MagicMock

import pytest

from src.infrastructure.data_sources.bars_datasource import BarsDataSource
from src.infrastructure.data_sources.csv_replay_datasource import CSVReplayTickSource


class FakeBarsSource(BarsDataSource):
    pair = "MNQ"

    def load_1m_bars(self):
        ts = int(datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc).timestamp())
        return [
            {"time": ts, "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.5, "volume": 10, "pair": "MNQ"},
            {"time": ts + 60, "open": 100.5, "high": 102.0, "low": 100.0,
             "close": 101.5, "volume": 20, "pair": "MNQ"},
        ]


@pytest.fixture
def no_sleep(monkeypatch):
    """Disable replay sleeps so tests run quickly and deterministically."""
    monkeypatch.setattr(
        "src.infrastructure.data_sources.csv_replay_datasource.time.sleep",
        lambda _s: None,
    )


class TestCSVReplayTickSource:

    def test_load_historical_ticks_returns_empty(self):
        ds = CSVReplayTickSource(FakeBarsSource())
        assert ds.load_historical_ticks() == []

    def test_pair_inherited_from_source(self):
        ds = CSVReplayTickSource(FakeBarsSource())
        assert ds.pair == "MNQ"

    def test_subscribe_emits_all_bars(self, no_sleep):
        ds = CSVReplayTickSource(FakeBarsSource())
        received = []
        done = Event()

        def callback(bar):
            received.append(bar)
            if len(received) == 2:
                done.set()

        ds.subscribe(callback)
        assert done.wait(timeout=1.0)
        assert len(received) == 2
        assert received[0]["time"] < received[1]["time"]

    def test_subscribe_speed_multiplier_scales_sleep(self, monkeypatch):
        ds = CSVReplayTickSource(FakeBarsSource(), speed_multiplier=2.0)
        sleeps = []
        import threading

        def _capture(s):
            if threading.current_thread().name == "csv_replay":
                sleeps.append(s)

        monkeypatch.setattr(
            "src.infrastructure.data_sources.csv_replay_datasource.time.sleep",
            _capture,
        )
        received = []
        ds.subscribe(received.append)
        # 60 seconds between bars at 2x speed = 30 seconds of sleep
        assert sleeps == [30.0]

    def test_subscribe_no_sleep_for_first_bar(self, monkeypatch):
        ds = CSVReplayTickSource(FakeBarsSource(), speed_multiplier=1.0)
        sleeps = []
        import threading

        def _capture(s):
            if threading.current_thread().name == "csv_replay":
                sleeps.append(s)

        monkeypatch.setattr(
            "src.infrastructure.data_sources.csv_replay_datasource.time.sleep",
            _capture,
        )
        received = []
        ds.subscribe(received.append)
        assert sleeps == [60.0]

    def test_subscribe_empty_bars(self, no_sleep):
        class EmptyBarsSource(BarsDataSource):
            pair = "MNQ"

            def load_1m_bars(self):
                return []

        ds = CSVReplayTickSource(EmptyBarsSource())
        received = []
        ds.subscribe(received.append)
        import time
        time.sleep(0.01)
        assert received == []

    def test_subscribe_callback_can_be_any_callable(self, no_sleep):
        ds = CSVReplayTickSource(FakeBarsSource())
        callback = MagicMock()
        ds.subscribe(callback)
        import time
        time.sleep(0.01)
        assert callback.call_count == 2
