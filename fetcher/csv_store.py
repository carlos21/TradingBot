"""
CSV store: reads and writes 1m OHLCV bars in the format expected by CSVDataSource.

CSV format (semicolon-delimited):
    Date;Time;Open;High;Low;Close;Volume
    DD/MM/YYYY;HH:MM:SS;float;float;float;float;int

All timestamps in the CSV are stored in the configured local timezone.
CSVDataSource then re-parses them and converts back to UTC internally.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set

from .types import Bar

logger = logging.getLogger(__name__)

_UTC = timezone.utc
_DATE_FMT = "%d/%m/%Y"
_TIME_FMT = "%H:%M:%S"
_TS_FMT   = f"{_DATE_FMT} {_TIME_FMT}"
_HEADER   = "Date;Time;Open;High;Low;Close;Volume\n"


class CSVStore:
    """
    Manages reading and writing 1m OHLCV bars to/from a CSV file.

    Handles:
    - Creating the file with the correct header on first write
    - Deduplication: never writes a bar whose timestamp already exists
    - Timezone conversion: converts UTC epoch timestamps to/from the
      configured local timezone when writing/reading
    """

    def __init__(self, filepath: str, pair: str, tz: str = "America/Chicago"):
        """
        Args:
            filepath: Path to the CSV file. Created automatically on first write.
            pair:     Pair name embedded in each bar (e.g., "MNQ").
            tz:       Timezone for Date/Time columns in the CSV.
                      Must match the 'tz' parameter in CSVDataSource when reading.
        """
        from zoneinfo import ZoneInfo
        self.filepath = Path(filepath)
        self.pair     = pair
        self.tz       = ZoneInfo(tz)
        self.tz_name  = tz

    # ── Public API ──────────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        """Return True if the CSV doesn't exist or has no data rows."""
        if not self.filepath.exists():
            return True
        # Count non-header lines
        with open(self.filepath, "r") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("Date"):
                    return False
        return True

    def get_last_timestamp(self) -> Optional[int]:
        """
        Return the UTC epoch (int) of the last bar in the CSV, or None if empty.

        Reads only the final line of the file for efficiency — O(1) regardless
        of how many bars the file contains.
        """
        if self.is_empty():
            return None

        last_line = self._read_last_line()
        return self._parse_ts(last_line)

    def read_timestamps(self) -> Set[int]:
        """
        Return the set of all UTC epoch timestamps already present in the CSV.

        Used for deduplication before appending. For very large files this
        streams line-by-line without loading all rows into memory at once.
        """
        if not self.filepath.exists():
            return set()

        timestamps: Set[int] = set()
        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("Date"):
                    continue
                ts = self._parse_ts(stripped)
                if ts is not None:
                    timestamps.add(ts)
        return timestamps

    def write_new_bars(self, bars: list) -> int:
        """
        Append new bars to the CSV, skipping any that already exist.

        Creates the file with the correct header if it doesn't exist yet.

        Args:
            bars: List of Bar dicts (UTC epoch timestamps).

        Returns:
            Number of bars actually written.
        """
        if not bars:
            return 0

        existing = self.read_timestamps()
        new_bars = sorted(
            [b for b in bars if b["time"] not in existing],
            key=lambda b: b["time"],
        )

        if not new_bars:
            logger.info("No new bars to write — all already present in CSV.")
            return 0

        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        if not self.filepath.exists() or self.filepath.stat().st_size == 0:
            self.filepath.write_text(_HEADER, encoding="utf-8")
            logger.info(f"Created new CSV: {self.filepath}")

        with open(self.filepath, "a", newline="", encoding="utf-8") as f:
            for bar in new_bars:
                f.write(self._bar_to_line(bar))

        logger.info(f"Appended {len(new_bars)} new bars to {self.filepath}")
        return len(new_bars)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _bar_to_line(self, bar: Bar) -> str:
        dt_utc   = datetime.fromtimestamp(bar["time"], tz=_UTC)
        dt_local = dt_utc.astimezone(self.tz)
        date_str = dt_local.strftime(_DATE_FMT)
        time_str = dt_local.strftime(_TIME_FMT)
        return (
            f"{date_str};{time_str};"
            f"{bar['open']};{bar['high']};{bar['low']};{bar['close']};"
            f"{bar['volume']}\n"
        )

    def _parse_ts(self, line: str) -> Optional[int]:
        """Parse a CSV line and return its UTC epoch timestamp, or None on error."""
        try:
            parts  = line.split(";")
            ts_str = f"{parts[0]} {parts[1]}"
            dt     = datetime.strptime(ts_str, _TS_FMT)
            dt_loc = dt.replace(tzinfo=self.tz)
            return int(dt_loc.astimezone(_UTC).timestamp())
        except (ValueError, IndexError):
            return None

    def _read_last_line(self) -> str:
        """Return the last non-empty line of the file (O(1) seek from end)."""
        with open(self.filepath, "rb") as f:
            try:
                f.seek(-2, 2)
                while f.read(1) != b"\n":
                    f.seek(-2, 1)
            except OSError:
                f.seek(0)
            return f.readline().decode("utf-8", errors="replace").strip()
