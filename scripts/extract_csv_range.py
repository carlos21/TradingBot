#!/usr/bin/env python3
"""
extract_csv_range.py

Read a semicolon-delimited OHLCV CSV with columns:
Date;Time;Open;High;Low;Close;Volume

Filter rows by a datetime range and write a new CSV (same header).
"""

import csv
import argparse
from datetime import datetime
from typing import Optional
from dateutil import parser as dtparser


DEFAULT_FILE_DTFORMAT = "%d/%m/%Y %H:%M:%S"  # matches your sample: 31/07/2024 15:12:00


def parse_any_dt(s: str, dayfirst: bool = True) -> datetime:
    """Parse a datetime string using python-dateutil if available, else try a few common formats."""
    if dtparser is not None:
        return dtparser.parse(s, dayfirst=dayfirst)

    # fallback formats (extend as needed)
    for fmt in ("%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%d/%m/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(
        f"Could not parse datetime {s!r}. Install python-dateutil or use a supported format."
    )


def parse_row_dt(date_str: str, time_str: str, file_dtformat: Optional[str], dayfirst: bool) -> datetime:
    combined = f"{date_str} {time_str}"
    if file_dtformat:  # explicit strptime pattern
        return datetime.strptime(combined, file_dtformat)
    # auto-detect
    return parse_any_dt(combined, dayfirst=dayfirst)


def main():
    ap = argparse.ArgumentParser(description="Extract a datetime range from a semicolon-delimited OHLCV CSV.")
    ap.add_argument("input", help="Path to input CSV (semicolon-delimited).")
    ap.add_argument("output", help="Path to output CSV (semicolon-delimited).")

    ap.add_argument("--start", required=True,
                    help="Start datetime (inclusive). Ex: '31/07/2024 15:12:00' or '2024-07-31 15:12:00'")
    ap.add_argument("--end", required=True,
                    help="End datetime (inclusive). Ex: '31/07/2024 15:17:00' or '2024-07-31 15:17:00'")

    # If you want the script to infer the file's Date/Time pattern, pass --file-dtformat auto
    ap.add_argument("--file-dtformat", dest="file_dtformat", default=DEFAULT_FILE_DTFORMAT,
                    help=f"Datetime format for combining Date+Time in the file (default: '{DEFAULT_FILE_DTFORMAT}'). "
                         "Use 'auto' to auto-detect with dateutil.")

    # Nice toggle for day-first vs month-first when auto-parsing
    df_group = ap.add_mutually_exclusive_group()
    df_group.add_argument("--dayfirst", dest="dayfirst", action="store_true",
                          help="Treat ambiguous dates as day-first (31/07/2024).")
    df_group.add_argument("--monthfirst", dest="dayfirst", action="store_false",
                          help="Treat ambiguous dates as month-first (07/31/2024).")
    ap.set_defaults(dayfirst=True)  # your sample is day-first

    args = ap.parse_args()

    # Parse inclusive bounds
    start_dt = parse_any_dt(args.start, dayfirst=args.dayfirst)
    end_dt   = parse_any_dt(args.end,   dayfirst=args.dayfirst)
    if end_dt < start_dt:
        raise SystemExit("End datetime must be >= start datetime.")

    # Normalize file_dtformat
    file_dtformat = None if (args.file_dtformat or "").lower() == "auto" else args.file_dtformat

    with open(args.input, newline="", encoding="utf-8") as fin, \
         open(args.output, "w", newline="", encoding="utf-8") as fout:

        reader = csv.DictReader(fin, delimiter=";")
        required_cols = ["Date", "Time", "Open", "High", "Low", "Close", "Volume"]
        for col in required_cols:
            if col not in (reader.fieldnames or []):
                raise SystemExit(f"Input file missing required column: {col}")

        writer = csv.DictWriter(fout, fieldnames=required_cols, delimiter=";")
        writer.writeheader()

        kept = 0
        for row in reader:
            try:
                row_dt = parse_row_dt(row["Date"], row["Time"], file_dtformat, args.dayfirst)
            except Exception as e:
                raise SystemExit(f"Failed parsing row datetime for {row!r}: {e}") from e

            if start_dt <= row_dt <= end_dt:
                writer.writerow({
                    "Date":   row["Date"],
                    "Time":   row["Time"],
                    "Open":   row["Open"],
                    "High":   row["High"],
                    "Low":    row["Low"],
                    "Close":  row["Close"],
                    "Volume": row.get("Volume", ""),
                })
                kept += 1

    print(f"Done. Wrote {kept} rows to {args.output}")


if __name__ == "__main__":
    main()