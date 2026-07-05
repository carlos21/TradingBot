"""Load 1m bars from CSV files for ZMQ streaming in e2e tests.

Reuses the parsing logic from CSVDataSource but without the full datasource
state machine — just raw bar extraction for a time window.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from zoneinfo import ZoneInfo

from dateutil import parser

DEFAULT_FMT = "%d/%m/%Y %H:%M:%S"
PAIR_FORMATS = {"EURUSD": "%Y.%m.%d %H:%M", "MNQ": "%d/%m/%Y %H:%M:%S"}
PAIR_TZS = {"EURUSD": "Europe/London", "MNQ": "America/Chicago"}


def load_bars(
    filepath: str | Path,
    pair: str = "MNQ",
    start_time: int | None = None,
    end_time: int | None = None,
    time_fmt: str | None = None,
    tz: str | None = None,
    fileobj: BinaryIO | None = None,
) -> list[dict]:
    """Load bars from a CSV file, optionally filtering by epoch time window.

    Args:
        filepath: Path to the CSV file (ignored if *fileobj* is provided).
        pair: Instrument pair (determines default format/timezone).
        start_time: Optional inclusive start epoch timestamp.
        end_time: Optional inclusive end epoch timestamp.
        time_fmt: Override CSV datetime format.
        tz: Override timezone name.
        fileobj: Optional file-like object to read from instead of *filepath*.

    Returns:
        List of bar dicts with keys: time, open, high, low, close, volume, pair.
    """
    fmt = time_fmt or PAIR_FORMATS.get(pair, DEFAULT_FMT)
    local_tz = ZoneInfo(tz or PAIR_TZS.get(pair, "UTC"))
    utc = ZoneInfo("UTC")

    bars: list[dict] = []
    with (fileobj or open(filepath, newline="")) as f:
        sample = f.read(2048)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        reader = csv.DictReader(f, dialect=dialect)

        for row in reader:
            ts = f"{row['Date']} {row['Time']}"
            try:
                dt = datetime.strptime(ts, fmt)
            except ValueError:
                dt = parser.parse(ts)

            dt_local = dt.replace(tzinfo=local_tz)
            dt_utc = dt_local.astimezone(utc)
            epoch = int(dt_utc.timestamp())

            if start_time is not None and epoch < start_time:
                continue
            if end_time is not None and epoch > end_time:
                continue

            bars.append(
                {
                    "time": epoch,
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row.get("Volume", 0)),
                    "pair": pair,
                }
            )

    return bars
