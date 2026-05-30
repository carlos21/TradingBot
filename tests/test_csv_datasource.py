"""Tests for src/data_sources/csv_datasource.py."""

import io
from datetime import datetime

import pytest

from src.infrastructure.data_sources.csv_datasource import CSVDataSource


CSV_CONTENT = """Date,Time,Open,High,Low,Close,Volume
01/01/2024,09:30,5000.0,5010.0,4990.0,5005.0,1000
01/01/2024,09:31,5005.0,5015.0,5000.0,5010.0,1500
01/01/2024,09:32,5010.0,5020.0,5005.0,5015.0,2000
"""


@pytest.fixture
def csv_source():
    fileobj = io.StringIO(CSV_CONTENT)
    return CSVDataSource(
        pair="MNQ",
        fileobj=fileobj,
        time_fmt="%d/%m/%Y %H:%M",
        tz="UTC",
    )


class TestCSVDataSourceInit:

    def test_loads_bars_on_init(self, csv_source):
        assert len(csv_source._bars) == 3

    def test_pair_assignment(self, csv_source):
        assert csv_source.pair == "MNQ"

    def test_default_time_fmt(self):
        fileobj = io.StringIO(CSV_CONTENT)
        ds = CSVDataSource(pair="MNQ", fileobj=fileobj, tz="UTC")
        assert ds.fmt == CSVDataSource.DEFAULT_FMT

    def test_datetime_initial_start_time(self):
        fileobj = io.StringIO(CSV_CONTENT)
        start = datetime(2024, 1, 1, 9, 30)
        ds = CSVDataSource(pair="MNQ", fileobj=fileobj, tz="UTC", initial_start_time=start)
        assert ds.initial_start_time == int(start.timestamp())


class TestCSVDataSourceHistoricalBars:

    def test_load_historical_1m(self, csv_source):
        bars = csv_source.load_historical_bars("1m")
        assert len(bars) == 3
        assert bars[0]["open"] == 5000.0
        assert bars[0]["high"] == 5010.0
        assert bars[0]["low"] == 4990.0
        assert bars[0]["close"] == 5005.0
        assert bars[0]["volume"] == 1000
        assert bars[0]["pair"] == "MNQ"

    def test_load_historical_with_start_time(self, csv_source):
        # All bars are at 09:30, 09:31, 09:32 UTC
        # Filter to only bars after 09:30
        bars = csv_source.load_historical_bars("1m", start_time=csv_source._bars[0]["time"] + 1)
        assert len(bars) == 2

    def test_bar_structure(self, csv_source):
        bars = csv_source.load_historical_bars("1m")
        required_keys = {"time", "open", "high", "low", "close", "volume", "pair"}
        for bar in bars:
            assert set(bar.keys()) >= required_keys


class TestCSVDataSourceReset:

    def test_reset_reseeds_bars(self, csv_source):
        csv_source.reset()
        assert csv_source.current_1m_index == 0
        assert csv_source._1m_buffer == []

    def test_reset_with_time_bounds(self, csv_source):
        # Reset with bounds that filter out first bar
        first_ts = csv_source._bars[0]["time"]
        csv_source.reset(start_time=first_ts + 1)
        assert len(csv_source._played_bars) == 2


class TestCSVDataSourceTimeframeAggregation:

    def test_5m_aggregation(self, csv_source):
        # 3 bars of 1m data → should still return 1 bar for 5m
        bars = csv_source.load_historical_bars("5m")
        assert len(bars) == 1
        assert bars[0]["open"] == 5000.0
        assert bars[0]["high"] == 5020.0
        assert bars[0]["low"] == 4990.0
        assert bars[0]["close"] == 5015.0


class TestCSVDataSourcePause:

    def test_pause_sets_stop_event(self, csv_source):
        csv_source.pause()
        assert csv_source._stop_event.is_set()


class TestCSVDataSourceSeek:

    def test_seek_resets_index(self, csv_source):
        csv_source.current_1m_index = 2
        csv_source.seek(0)
        assert csv_source.current_1m_index == 0


class TestCSVDataSourceSubscribe:

    def test_subscribe_emits_end_when_no_bars(self):
        empty_content = "Date,Time,Open,High,Low,Close,Volume\n"
        fileobj = io.StringIO(empty_content)
        ds = CSVDataSource(pair="MNQ", fileobj=fileobj, tz="UTC")
        received = []
        ds.subscribe(lambda msg: received.append(msg), from_time=0)
        assert any(m.get("_end") for m in received)
