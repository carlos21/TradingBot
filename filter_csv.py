#!/usr/bin/env python3
import argparse
import pandas as pd

def filter_year(input_csv: str, output_csv: str, year: int, chunksize: int = 10**6):
    """
    Read input_csv in chunks, keep only rows whose timestamp year == year,
    and append them to output_csv.
    """
    first_chunk = True
    # We assume the timestamp is in the first (unnamed) column
    for chunk in pd.read_csv(
        input_csv,
        index_col=0,
        parse_dates=True,
        chunksize=chunksize,
        infer_datetime_format=True,
    ):
        # chunk.index is a DatetimeIndex
        filtered = chunk[chunk.index.year == year]
        filtered.to_csv(
            output_csv,
            mode="w" if first_chunk else "a",
            header=first_chunk,
        )
        first_chunk = False

def main():
    p = argparse.ArgumentParser(
        description="Filter a CSV of OHLC data to a single year."
    )
    p.add_argument("input_csv", help="Path to the input CSV file")
    p.add_argument("year", type=int, help="Year to filter (e.g. 2000)")
    p.add_argument("output_csv", help="Path for the filtered output CSV")
    args = p.parse_args()

    filter_year(args.input_csv, args.output_csv, args.year)
    print(f"Written rows from {args.year} to {args.output_csv}")

if __name__ == "__main__":
    main()