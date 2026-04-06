"""Tests for BarAggregator utility."""

import pytest
from src.utils.bar_aggregator import BarAggregator


class TestParseTimeframe:
    def test_minutes(self):
        assert BarAggregator.parse_timeframe("5m") == 300
        assert BarAggregator.parse_timeframe("1m") == 60
        assert BarAggregator.parse_timeframe("15m") == 900

    def test_hours(self):
        assert BarAggregator.parse_timeframe("1h") == 3600
        assert BarAggregator.parse_timeframe("4h") == 14400

    def test_days(self):
        assert BarAggregator.parse_timeframe("1d") == 86400

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            BarAggregator.parse_timeframe("invalid")

    def test_empty(self):
        with pytest.raises(ValueError):
            BarAggregator.parse_timeframe("")

    def test_zero_value(self):
        with pytest.raises(ValueError):
            BarAggregator.parse_timeframe("0m")


class TestAggregate:
    def test_basic_aggregation(self):
        bars = [
            {"time": 1000, "open": 100, "high": 105, "low": 98, "close": 102, "volume": 10, "pair": "NQ"},
            {"time": 1060, "open": 102, "high": 108, "low": 101, "close": 106, "volume": 15, "pair": "NQ"},
            {"time": 1120, "open": 106, "high": 110, "low": 105, "close": 108, "volume": 20, "pair": "NQ"},
        ]
        result = BarAggregator.aggregate(bars)

        assert result["time"] == 1000
        assert result["open"] == 100
        assert result["high"] == 110
        assert result["low"] == 98
        assert result["close"] == 108
        assert result["volume"] == 45
        assert result["pair"] == "NQ"

    def test_empty_list(self):
        assert BarAggregator.aggregate([]) is None

    def test_single_bar(self):
        bars = [{"time": 1000, "open": 100, "high": 105, "low": 98, "close": 102, "volume": 10, "pair": "NQ"}]
        result = BarAggregator.aggregate(bars)

        assert result["open"] == 100
        assert result["high"] == 105
        assert result["low"] == 98
        assert result["close"] == 102


class TestAggregateWithWindow:
    def test_with_window_params(self):
        bars = [
            {"time": 1000, "open": 100, "high": 105, "low": 98, "close": 102, "volume": 10, "pair": "NQ"},
            {"time": 1060, "open": 102, "high": 108, "low": 101, "close": 106, "volume": 15, "pair": "NQ"},
        ]
        result = BarAggregator.aggregate_with_window(bars, window_start=900, window_secs=300)

        assert result["time"] == 1200  # 900 + 300
        assert result["high"] == 108
        assert result["low"] == 98


class TestBucketByTimeframe:
    def test_bucket_5m(self):
        bars = [
            {"time": 60, "open": 100, "high": 105, "low": 98, "close": 102, "volume": 10, "pair": "NQ"},
            {"time": 120, "open": 102, "high": 108, "low": 101, "close": 106, "volume": 15, "pair": "NQ"},
            {"time": 360, "open": 106, "high": 110, "low": 105, "close": 108, "volume": 20, "pair": "NQ"},
            {"time": 420, "open": 108, "high": 112, "low": 107, "close": 110, "volume": 25, "pair": "NQ"},
        ]
        buckets = BarAggregator.bucket_by_timeframe(bars, "5m")

        # 5m = 300s windows
        # bars[0] at 60 -> window 0
        # bars[1] at 120 -> window 0
        # bars[2] at 360 -> window 300
        # bars[3] at 420 -> window 300
        assert len(buckets) == 2
        assert len(buckets[0]) == 2
        assert len(buckets[300]) == 2


class TestMergePartial:
    def test_with_buffered_bars(self):
        partial = {"time": 360, "open": 110, "high": 115, "low": 108, "close": 112, "volume": 30, "pair": "NQ"}
        buffered = [
            {"time": 60, "open": 100, "high": 105, "low": 98, "close": 102, "volume": 10, "pair": "NQ"},
            {"time": 120, "open": 102, "high": 108, "low": 101, "close": 106, "volume": 15, "pair": "NQ"},
        ]
        result = BarAggregator.merge_partial(partial, buffered, window_start=0)

        assert result["time"] == 0
        assert result["open"] == 100  # from first buffered
        assert result["high"] == 115  # max of buffered + partial
        assert result["low"] == 98    # min of buffered + partial
        assert result["close"] == 112 # from partial
        assert result["volume"] == 55 # 10 + 15 + 30

    def test_no_buffered(self):
        partial = {"time": 360, "open": 110, "high": 115, "low": 108, "close": 112, "volume": 30, "pair": "NQ"}
        result = BarAggregator.merge_partial(partial, [], window_start=300)

        assert result["time"] == 300
        assert result["open"] == 110
        assert result["high"] == 115
