"""Tests for scripts.extract_csv_range."""

import csv
from pathlib import Path

import pytest

from scripts.extract_csv_range import parse_any_dt, parse_row_dt


class TestExtractCsvRange:
    @pytest.fixture
    def sample_csv(self, tmp_path: Path) -> Path:
        csv_path = tmp_path / "input.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["Date", "Time", "Open", "High", "Low", "Close", "Volume"],
                delimiter=";",
            )
            writer.writeheader()
            for i in range(5):
                writer.writerow(
                    {
                        "Date": f"0{i+1}/01/2024",
                        "Time": f"10:0{i}:00",
                        "Open": "100",
                        "High": "101",
                        "Low": "99",
                        "Close": "100.5",
                        "Volume": "10",
                    }
                )
        return csv_path

    def test_extract_range(self, sample_csv: Path, tmp_path: Path):
        from scripts import extract_csv_range as mod
        output = tmp_path / "out.csv"
        mod.parse_any_dt = parse_any_dt
        mod.parse_row_dt = parse_row_dt

        # Directly exercise the core filter logic.
        kept = 0
        with open(sample_csv, newline="", encoding="utf-8") as fin, \
             open(output, "w", newline="", encoding="utf-8") as fout:
            reader = csv.DictReader(fin, delimiter=";")
            writer = csv.DictWriter(
                fout,
                fieldnames=["Date", "Time", "Open", "High", "Low", "Close", "Volume"],
                delimiter=";",
            )
            writer.writeheader()
            start_dt = parse_any_dt("02/01/2024 10:01:00")
            end_dt = parse_any_dt("04/01/2024 10:03:00")
            for row in reader:
                row_dt = parse_row_dt(row["Date"], row["Time"], None, True)
                if start_dt <= row_dt <= end_dt:
                    writer.writerow(row)
                    kept += 1

        assert kept == 3

    def test_parse_row_dt_with_explicit_format(self):
        dt = parse_row_dt("2024-01-02", "10:01:00", "%Y-%m-%d %H:%M:%S", True)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 2
