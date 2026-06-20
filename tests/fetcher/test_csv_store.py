"""Tests for fetcher.csv_store."""

from pathlib import Path

from fetcher.csv_store import CSVStore


def _bar(time: int, pair: str = "MNQ") -> dict:
    return {
        "time": time,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 10,
        "pair": pair,
    }


class TestCSVStore:
    def test_is_empty_for_missing_file(self, tmp_path: Path):
        store = CSVStore(filepath=str(tmp_path / "missing.csv"), pair="MNQ")
        assert store.is_empty() is True

    def test_is_empty_for_header_only(self, tmp_path: Path):
        csv = tmp_path / "header_only.csv"
        csv.write_text("Date;Time;Open;High;Low;Close;Volume\n", encoding="utf-8")
        store = CSVStore(filepath=str(csv), pair="MNQ")
        assert store.is_empty() is True

    def test_write_creates_header_and_parent_directory(self, tmp_path: Path):
        csv = tmp_path / "nested" / "bars.csv"
        store = CSVStore(filepath=str(csv), pair="MNQ")
        assert store.write_new_bars([_bar(1700000000)]) == 1
        assert csv.exists()
        lines = csv.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "Date;Time;Open;High;Low;Close;Volume"
        assert len(lines) == 2

    def test_write_deduplicates_existing_timestamps(self, tmp_path: Path):
        csv = tmp_path / "bars.csv"
        store = CSVStore(filepath=str(csv), pair="MNQ")
        assert store.write_new_bars([_bar(1700000000), _bar(1700000060)]) == 2
        assert store.write_new_bars([_bar(1700000000), _bar(1700000120)]) == 1

    def test_get_last_timestamp_returns_utc_epoch(self, tmp_path: Path):
        csv = tmp_path / "bars.csv"
        store = CSVStore(filepath=str(csv), pair="MNQ", tz="America/Chicago")
        # 2023-11-14 12:00:00 UTC
        store.write_new_bars([_bar(1699963200)])
        assert store.get_last_timestamp() == 1699963200

    def test_malformed_lines_are_ignored(self, tmp_path: Path):
        csv = tmp_path / "bars.csv"
        csv.write_text(
            "Date;Time;Open;High;Low;Close;Volume\n"
            "bad;line;1;2;3;4;5\n"
            "14/11/2023;06:00:00;100;101;99;100.5;10\n",
            encoding="utf-8",
        )
        store = CSVStore(filepath=str(csv), pair="MNQ", tz="America/Chicago")
        assert store.is_empty() is False
        timestamps = store.read_timestamps()
        # The first data line is unparseable, the second is parseable.
        assert len(timestamps) == 1

    def test_read_timestamps_empty_file(self, tmp_path: Path):
        store = CSVStore(filepath=str(tmp_path / "missing.csv"), pair="MNQ")
        assert store.read_timestamps() == set()

    def test_timezone_round_trip(self, tmp_path: Path):
        csv = tmp_path / "bars.csv"
        store = CSVStore(filepath=str(csv), pair="MNQ", tz="America/Chicago")
        store.write_new_bars([_bar(1700000000)])
        content = csv.read_text(encoding="utf-8")
        # 1700000000 UTC -> 2023-11-14 16:13:20 CST
        assert "14/11/2023;16:13:20" in content
