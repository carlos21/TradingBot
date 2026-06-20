"""Tests for fetcher.sync."""

from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest

from fetcher.base import FetchProvider
from fetcher.csv_store import CSVStore
from fetcher.sync import DataSyncer
from fetcher.types import Bar


class _FakeProvider(FetchProvider):
    def __init__(self, bars: List[Bar] | None = None, max_days: int = -1):
        self._bars = bars or []
        self._max_days = max_days
        self.calls = []

    @property
    def name(self) -> str:
        return "FakeProvider"

    @property
    def max_history_days(self) -> int:
        return self._max_days

    def fetch_bars(self, symbol: str, start: datetime, end: datetime) -> List[Bar]:
        self.calls.append((symbol, start, end))
        return self._bars


def _bar(time: int) -> Bar:
    return {
        "time": time,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 10,
        "pair": "MNQ",
    }


class TestDataSyncer:
    def test_empty_csv_uses_lookback(self, tmp_path: Path):
        csv = tmp_path / "bars.csv"
        end = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        provider = _FakeProvider(bars=[_bar(1705315200)])
        syncer = DataSyncer(provider, CSVStore(str(csv), "MNQ", tz="UTC"))

        written = syncer.sync("MNQ", lookback_days=5, end=end)

        assert written == 1
        assert len(provider.calls) == 1
        # Lookback 5 days from 2024-01-15 12:00 UTC.
        assert provider.calls[0][1] == datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)

    def test_up_to_date_csv_returns_zero(self, tmp_path: Path):
        csv = tmp_path / "bars.csv"
        store = CSVStore(str(csv), "MNQ", tz="UTC")
        end = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        # Last bar only one minute before end -> nothing to fetch.
        store.write_new_bars([_bar(int(end.timestamp()) - 60)])
        provider = _FakeProvider()
        syncer = DataSyncer(provider, store)

        assert syncer.sync("MNQ", end=end) == 0
        assert len(provider.calls) == 0

    def test_provider_error_is_logged_and_raised(self, tmp_path: Path):
        csv = tmp_path / "bars.csv"
        provider = _FakeProvider()
        provider.fetch_bars = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("network down")
        )
        syncer = DataSyncer(provider, CSVStore(str(csv), "MNQ", tz="UTC"))

        with pytest.raises(RuntimeError, match="network down"):
            syncer.sync("MNQ", lookback_days=1)

    def test_max_history_days_clamping(self, tmp_path: Path):
        csv = tmp_path / "bars.csv"
        end = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        provider = _FakeProvider(max_days=2)
        syncer = DataSyncer(provider, CSVStore(str(csv), "MNQ", tz="UTC"))

        syncer.sync("MNQ", lookback_days=10, end=end)

        # Empty CSV -> lookback clamped to provider max 2 days.
        assert provider.calls[0][1] == datetime(2024, 1, 13, 12, 0, tzinfo=timezone.utc)

    def test_negative_lookback_raises(self, tmp_path: Path):
        csv = tmp_path / "bars.csv"
        syncer = DataSyncer(_FakeProvider(), CSVStore(str(csv), "MNQ", tz="UTC"))

        with pytest.raises(ValueError, match="non-negative"):
            syncer.sync("MNQ", lookback_days=-1)
