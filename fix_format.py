#!/usr/bin/env python3
import argparse
import csv
from datetime import datetime

def fix_format(input_csv: str, output_csv: str, time_format: str):
    """
    Read a CSV whose first column is an ISO8601 datetime with offset
    (e.g. '2000-05-30 17:27:00-05:00') plus Open,High,Low,Close,
    and write out a semicolon-delimited CSV with columns:
      Date;Time;Open;High;Low;Close;Volume
    where Volume is set to 0, and Date/Time match the given time_format.
    """
    # split the format into date / time parts
    try:
        date_fmt, time_fmt = time_format.split(" ", 1)
    except ValueError:
        raise ValueError("time_format must include both date and time parts, separated by a space")

    with open(input_csv, newline="") as infile, open(output_csv, "w", newline="") as outfile:
        reader = csv.reader(infile)
        writer = csv.writer(outfile, delimiter=";")

        # skip the original header
        next(reader, None)
        # write new header
        writer.writerow(["Date", "Time", "Open", "High", "Low", "Close", "Volume"])

        for row in reader:
            iso_ts = row[0]
            # parse ISO8601 (with offset)
            dt = datetime.fromisoformat(iso_ts)
            # reformat
            combined = dt.strftime(time_format)
            date_part, time_part = combined.split(" ", 1)

            open_, high, low, close = row[1:5]
            volume = "0"  # or you can pull from row if it exists

            writer.writerow([
                date_part,
                time_part,
                open_,
                high,
                low,
                close,
                volume
            ])

def main():
    p = argparse.ArgumentParser(
        description="Reformat CSV to Date;Time;Open;High;Low;Close;Volume"
    )
    p.add_argument("input_csv", help="Path to the raw CSV")
    p.add_argument("output_csv", help="Where to write the fixed CSV")
    p.add_argument(
        "--time-format",
        required=True,
        help=(
            "The strptime/strftime format your loader expects, e.g. "
            "'%Y.%m.%d %H:%M' or '%d/%m/%Y %H:%M:%S'"
        ),
    )
    args = p.parse_args()

    fix_format(args.input_csv, args.output_csv, args.time_format)
    print(f"Reformatted → {args.output_csv}")

if __name__ == "__main__":
    main()