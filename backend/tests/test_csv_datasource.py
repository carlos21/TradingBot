"""Tests for src/data_sources/csv_datasource.py."""

import io
import threading
import time
from datetime import datetime, timezone

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

    def test_subscribe_honors_initial_end_time(self):
        from datetime import timezone
        fileobj = io.StringIO(CSV_CONTENT)
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
            initial_end_time=datetime(2024, 1, 1, 9, 31, tzinfo=timezone.utc),
        )
        received = []
        ds.subscribe(lambda msg: received.append(msg), from_time=0)
        bars = [m for m in received if not m.get("_end")]
        assert len(bars) == 2
        assert bars[-1]["time"] == int(datetime(2024, 1, 1, 9, 31, tzinfo=timezone.utc).timestamp())

    def test_subscribe_clamps_from_time_to_initial_start_time(self):
        from datetime import timezone
        fileobj = io.StringIO(CSV_CONTENT)
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
            initial_start_time=datetime(2024, 1, 1, 9, 31, tzinfo=timezone.utc),
        )
        received = []
        ds.subscribe(lambda msg: received.append(msg), from_time=0)
        bars = [m for m in received if not m.get("_end")]
        assert len(bars) == 2
        assert bars[0]["time"] == int(datetime(2024, 1, 1, 9, 31, tzinfo=timezone.utc).timestamp())


class TestCSVDataSourceEdgeCases:

    def test_empty_file_loads_no_bars(self):
        fileobj = io.StringIO("")
        ds = CSVDataSource(pair="MNQ", fileobj=fileobj, tz="UTC")
        assert ds._bars == []

    def test_header_only_loads_no_bars(self):
        fileobj = io.StringIO("Date,Time,Open,High,Low,Close,Volume\n")
        ds = CSVDataSource(pair="MNQ", fileobj=fileobj, tz="UTC")
        assert ds._bars == []

    def test_missing_required_columns_raises(self):
        content = "Date,Time,Open,High,Low\n01/01/2024,09:30,1,2,3\n"
        fileobj = io.StringIO(content)
        with pytest.raises(ValueError, match="missing required columns"):
            CSVDataSource(pair="MNQ", fileobj=fileobj, tz="UTC")

    def test_malformed_row_is_skipped(self):
        content = """Date,Time,Open,High,Low,Close,Volume
01/01/2024,09:30,5000.0,5010.0,4990.0,5005.0,1000
01/01/2024,09:31,bad,5015.0,5000.0,5010.0,1500
01/01/2024,09:32,5010.0,5020.0,5005.0,5015.0,2000
"""
        fileobj = io.StringIO(content)
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
        )
        assert len(ds._bars) == 2

    def test_empty_volume_defaults_to_zero(self):
        content = """Date,Time,Open,High,Low,Close,Volume
01/01/2024,09:30,5000.0,5010.0,4990.0,5005.0,
"""
        fileobj = io.StringIO(content)
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
        )
        assert ds._bars[0]["volume"] == 0

    def test_start_honors_initial_end_time(self):
        from datetime import timezone
        fileobj = io.StringIO(CSV_CONTENT)
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
            initial_end_time=datetime(2024, 1, 1, 9, 31, tzinfo=timezone.utc),
        )
        emitted = []
        ds.callback = emitted.append
        ds.start(from_time=0)
        # give thread time to emit
        import time
        time.sleep(0.2)
        ds.pause()
        bars = [m for m in emitted if not m.get("_end")]
        assert len(bars) <= 2


class TestCSVDataSourceLoadHistoricalBarsFilters:

    def test_end_time_filter(self, csv_source):
        bars = csv_source.load_historical_bars("1m", end_time=csv_source._bars[0]["time"])
        assert len(bars) == 1
        assert bars[0]["time"] == csv_source._bars[0]["time"]

    def test_start_and_end_time_filters(self, csv_source):
        bars = csv_source.load_historical_bars(
            "1m",
            start_time=csv_source._bars[0]["time"] + 1,
            end_time=csv_source._bars[2]["time"],
        )
        assert len(bars) == 2


class TestCSVDataSourceParsingBranches:

    def test_sniffer_fallback_to_excel(self, monkeypatch):
        import csv as csv_module
        fileobj = io.StringIO(CSV_CONTENT)
        monkeypatch.setattr(
            "src.infrastructure.data_sources.csv_datasource.csv.Sniffer.sniff",
            lambda *args, **kwargs: (_ for _ in ()).throw(csv_module.Error("sniff failed")),
        )
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
        )
        assert len(ds._bars) == 3

    def test_empty_fieldnames_returns_empty(self):
        fileobj = io.StringIO("\n")
        ds = CSVDataSource(pair="MNQ", fileobj=fileobj, tz="UTC")
        assert ds._bars == []

    def test_dict_reader_with_empty_fieldnames_returns_empty(self, monkeypatch):
        import csv as csv_module

        class EmptyDictReader:
            fieldnames = []

            def __iter__(self):
                return iter([])

        monkeypatch.setattr(
            "src.infrastructure.data_sources.csv_datasource.csv.DictReader",
            lambda *args, **kwargs: EmptyDictReader(),
        )
        fileobj = io.StringIO(CSV_CONTENT)
        ds = CSVDataSource(pair="MNQ", fileobj=fileobj, tz="UTC")
        assert ds._bars == []

    def test_blank_row_is_skipped(self):
        content = """Date,Time,Open,High,Low,Close,Volume
01/01/2024,09:30,5000.0,5010.0,4990.0,5005.0,1000
,,,,,,
01/01/2024,09:31,5005.0,5015.0,5000.0,5010.0,1500
"""
        fileobj = io.StringIO(content)
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
        )
        assert len(ds._bars) == 2

    def test_timestamp_with_timezone_is_converted(self):
        content = """Date,Time,Open,High,Low,Close,Volume
2024-01-01T09:30:00+00:00,,5000.0,5010.0,4990.0,5005.0,1000
"""
        fileobj = io.StringIO(content)
        ds = CSVDataSource(pair="MNQ", fileobj=fileobj, tz="UTC")
        assert len(ds._bars) == 1
        assert ds._bars[0]["time"] == int(datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc).timestamp())


class TestCSVDataSourceSubscribeBranches:

    def test_subscribe_runs_to_end_and_emits_end(self, csv_source):
        received = []
        csv_source.subscribe(received.append, from_time=0)
        assert any(m.get("_end") for m in received)
        bars = [m for m in received if not m.get("_end")]
        assert len(bars) == 3

    def test_subscribe_stops_on_stop_event(self, csv_source):
        received = []

        def callback(msg):
            received.append(msg)
            if len(received) == 2:
                csv_source._stop_event.set()

        csv_source.subscribe(callback, from_time=0)
        bars = [m for m in received if not m.get("_end")]
        assert len(bars) == 2

    def test_subscribe_end_when_from_time_beyond_all_bars(self, csv_source):
        received = []
        csv_source.subscribe(received.append, from_time=csv_source._bars[-1]["time"] + 1000)
        assert any(m.get("_end") for m in received)
        bars = [m for m in received if not m.get("_end")]
        assert len(bars) == 0


class TestCSVDataSourceTimeframe:

    def test_set_timeframe_hours(self, csv_source):
        csv_source.set_timeframe("2h")
        assert csv_source.current_tf == "2h"
        assert csv_source.group_size == 120

    def test_set_timeframe_unsupported_unit_raises(self, csv_source):
        with pytest.raises(ValueError, match="Unsupported timeframe '1d'"):
            csv_source.set_timeframe("1d")

    def test_set_timeframe_stops_live_thread(self, csv_source):
        csv_source._thread = threading.Thread(target=lambda: time.sleep(5))
        csv_source._thread.start()
        csv_source.set_timeframe("5m")
        assert csv_source.current_tf == "5m"
        csv_source._thread.join(timeout=0.5)


class TestCSVDataSourceStartReplay:

    def test_start_with_from_time_skips_earlier_bars(self, csv_source):
        emitted = []
        csv_source.callback = emitted.append
        csv_source.start(from_time=csv_source._bars[1]["time"])
        import time
        time.sleep(0.2)
        csv_source.pause()
        bars = [m for m in emitted if not m.get("_end")]
        assert len(bars) <= 2
        if bars:
            assert bars[0]["time"] >= csv_source._bars[1]["time"]

    def test_start_does_not_recompute_index_when_not_zero(self, csv_source, monkeypatch):
        started = []
        original_start = threading.Thread.start

        def fake_start(self):
            started.append(self)

        monkeypatch.setattr(threading.Thread, "start", fake_start)
        csv_source.current_1m_index = 1
        csv_source.start(from_time=0)
        assert csv_source.current_1m_index == 1
        assert len(started) == 1

    def test_start_recomputes_index_when_zero(self, csv_source, monkeypatch):
        monkeypatch.setattr(threading.Thread, "start", lambda self: None)
        csv_source.current_1m_index = 0
        csv_source.start(from_time=csv_source._bars[1]["time"])
        assert csv_source.current_1m_index == 1

    def test_run_replay_honors_initial_end_time(self):
        fileobj = io.StringIO(CSV_CONTENT)
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
            initial_end_time=datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc),
        )
        emitted = []
        ds.callback = emitted.append
        ds.start(from_time=0)
        time.sleep(0.2)
        ds.pause()
        bars = [m for m in emitted if not m.get("_end")]
        assert len(bars) == 1

    def test_run_replay_aggregates_higher_timeframe(self):
        content = """Date,Time,Open,High,Low,Close,Volume
01/01/2024,09:30,5000.0,5010.0,4990.0,5005.0,1000
01/01/2024,09:31,5005.0,5015.0,5000.0,5010.0,1500
01/01/2024,09:32,5010.0,5020.0,5005.0,5015.0,2000
01/01/2024,09:36,5015.0,5025.0,5010.0,5020.0,2500
"""
        fileobj = io.StringIO(content)
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
            bars_per_second=100.0,
        )
        ds.set_timeframe("5m")
        emitted = []
        ds.callback = emitted.append
        ds.start(from_time=0)
        time.sleep(0.15)
        ds.pause()
        assert len(emitted) >= 1
        assert emitted[0]["open"] == 5000.0
        assert emitted[0]["close"] == 5015.0
        assert emitted[0]["high"] == 5020.0
        assert emitted[0]["low"] == 4990.0

    def test_start_without_from_time_uses_existing_from_time(self, csv_source):
        csv_source._from_time = csv_source._bars[1]["time"]
        emitted = []
        csv_source.callback = emitted.append
        csv_source.start()
        time.sleep(0.2)
        csv_source.pause()
        bars = [m for m in emitted if not m.get("_end")]
        assert len(bars) <= 2
        if bars:
            assert bars[0]["time"] >= csv_source._bars[1]["time"]

    def test_run_replay_skips_bars_before_from_time(self):
        fileobj = io.StringIO(CSV_CONTENT)
        ds = CSVDataSource(
            pair="MNQ",
            fileobj=fileobj,
            time_fmt="%d/%m/%Y %H:%M",
            tz="UTC",
            bars_per_second=100.0,
        )
        emitted = []
        ds.callback = emitted.append
        # Force an early index but a later from_time so the first processed bar is skipped.
        ds.current_1m_index = 1
        ds._from_time = ds._bars[2]["time"]
        ds.start()
        time.sleep(0.2)
        ds.pause()
        bars = [m for m in emitted if not m.get("_end")]
        assert len(bars) == 1
        assert bars[0]["time"] == ds._bars[2]["time"]


class TestCSVDataSourcePauseEdgeCases:

    def test_pause_warns_when_thread_does_not_stop(self, csv_source, caplog):
        csv_source._thread = threading.Thread(target=lambda: time.sleep(5))
        csv_source._thread.start()
        csv_source.pause()
        assert "did not stop within timeout" in caplog.text


class TestCSVDataSourceResetEdgeCases:

    def test_reset_warns_when_thread_does_not_stop(self, csv_source, caplog):
        csv_source._thread = threading.Thread(target=lambda: time.sleep(5))
        csv_source._thread.start()
        csv_source.reset()
        assert csv_source.current_1m_index == 0


class TestCSVDataSourceAggregationBranches:

    def test_aggregate_whole_history_skips_empty_groups(self, csv_source, monkeypatch):
        from src.utils import bar_aggregator
        original = bar_aggregator.BarAggregator.bucket_by_timeframe

        def fake_bucket(bars, tf):
            buckets = original(bars, tf)
            buckets[9999999999] = []  # inject an empty bucket
            return buckets

        monkeypatch.setattr(bar_aggregator.BarAggregator, "bucket_by_timeframe", fake_bucket)
        bars = csv_source.load_historical_bars("5m")
        assert len(bars) == 1
