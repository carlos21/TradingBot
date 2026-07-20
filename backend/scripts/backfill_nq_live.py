#!/usr/bin/env python3
"""
backfill_nq_live.py

Fetch MNQ 1m bars from Databento for a specific date range and insert only
the missing candles into the live CSV (default: csvs/NQ_live.csv).

Unlike `fetcher.run` (which only appends after the last bar), this script
backfills a mid-file gap: existing lines are kept verbatim, new bars are
merged in chronological order, and the file is rewritten atomically.

The CSV keeps its canonical format:  Date;Time;Open;High;Low;Close;Volume
with DD/MM/YYYY dates and times in the configured local timezone
(default America/Chicago — must match 'tz' in CSVDataSource).

EXAMPLES
--------
  # Backfill the 2026-04-03 → 2026-04-05 gap (defaults):
  cd backend && poetry run python -m scripts.backfill_nq_live.py

  # See what would be written without touching the file:
  poetry run python -m scripts.backfill_nq_live.py --dry-run

  # Custom range / file:
  poetry run python -m scripts.backfill_nq_live.py \
      --start 2026-04-03 --end 2026-04-05 --output ../csvs/NQ_live.csv

Requires DATABENTO_API_KEY in the environment or in the project-root .env
(or pass --api-key).
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# The fetcher package lives under backend/
_BACKEND_DIR   = Path(__file__).resolve().parent.parent
_PROJECT_ROOT  = _BACKEND_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed — fall back to plain env vars

from fetcher.csv_store import CSVStore  # noqa: E402
from fetcher.providers.databento_provider import DatabentoProvider  # noqa: E402

logger = logging.getLogger(__name__)

_UTC = timezone.utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.backfill_nq_live",
        description="Backfill missing MNQ 1m candles in a CSV from Databento.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--start", default="2026-04-03", metavar="YYYY-MM-DD",
        help="First calendar date to fetch, in --tz local time (inclusive). "
             "[default: 2026-04-03]",
    )
    parser.add_argument(
        "--end", default="2026-04-05", metavar="YYYY-MM-DD",
        help="Last calendar date to fetch, in --tz local time (inclusive). "
             "[default: 2026-04-05]",
    )
    parser.add_argument(
        "--output", default=str(_PROJECT_ROOT / "csvs" / "NQ_live.csv"),
        help="CSV file to update. [default: csvs/NQ_live.csv]",
    )
    parser.add_argument(
        "--symbol", default=os.environ.get("FETCH_SYMBOL_DATABENTO", "NQ.c.0"),
        help="Databento symbol. [env: FETCH_SYMBOL_DATABENTO, default: NQ.c.0]",
    )
    parser.add_argument(
        "--tz", default=os.environ.get("FETCH_TZ", "America/Chicago"),
        help="Timezone of the CSV Date/Time columns. "
             "[env: FETCH_TZ, default: America/Chicago]",
    )
    parser.add_argument(
        "--pair", default=os.environ.get("FETCH_PAIR", "MNQ"),
        help="Pair name embedded in each bar dict. [env: FETCH_PAIR, default: MNQ]",
    )
    parser.add_argument(
        "--api-key", default=os.environ.get("DATABENTO_API_KEY"), metavar="KEY",
        help="Databento API key. [env: DATABENTO_API_KEY]",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and report what would be written, without touching the CSV.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    tz = ZoneInfo(args.tz)
    try:
        start_local = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=tz)
        end_local   = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=tz) + timedelta(days=1)
    except ValueError:
        print("Error: --start/--end must be in YYYY-MM-DD format.", file=sys.stderr)
        sys.exit(2)

    if start_local >= end_local:
        print("Error: --start must be on or before --end.", file=sys.stderr)
        sys.exit(2)

    start_utc = start_local.astimezone(_UTC)
    end_utc   = end_local.astimezone(_UTC)

    output = Path(args.output)
    if not output.exists():
        print(f"Error: CSV not found: {output}", file=sys.stderr)
        sys.exit(1)

    try:
        provider = DatabentoProvider(api_key=args.api_key)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Provider : {provider.name}")
    print(f"Symbol   : {args.symbol}")
    print(f"Range    : {args.start} → {args.end} ({args.tz})  "
          f"=  {start_utc.isoformat()} → {end_utc.isoformat()} UTC")
    print(f"Output   : {output}")
    print()

    # ── Fetch ────────────────────────────────────────────────────────────────
    bars = provider.fetch_bars(args.symbol, start_utc, end_utc)
    if not bars:
        print("Provider returned 0 bars for the requested range. Nothing to do.")
        return

    # ── Load existing rows (verbatim) and dedup ──────────────────────────────
    store = CSVStore(filepath=str(output), pair=args.pair, tz=args.tz)

    header_lines = []          # unparsable lines (the header), written first
    rows = []                  # (utc_epoch, original line)
    existing_ts = set()
    with open(output, "r", encoding="utf-8") as f:
        for line in f:
            ts = store._parse_ts(line)
            if ts is None:
                header_lines.append(line)
                continue
            existing_ts.add(ts)
            rows.append((ts, line))

    new_bars = [b for b in bars if b["time"] not in existing_ts]

    print(f"Fetched  : {len(bars)} bars from Databento")
    print(f"Present  : {len(bars) - len(new_bars)} already in CSV (skipped)")
    print(f"Missing  : {len(new_bars)} new bars to write")

    if args.dry_run:
        for b in new_bars[:3] + new_bars[-3:] if len(new_bars) > 6 else new_bars:
            print("  would add:", store._bar_to_line(b), end="")
        print("\nDry run — CSV left unchanged.")
        return

    if not new_bars:
        print("CSV is already complete for this range.")
        return

    # ── Merge in chronological order (stable sort keeps existing order) ──────
    rows.extend((b["time"], store._bar_to_line(b)) for b in new_bars)
    rows.sort(key=lambda r: r[0])

    # ── Atomic rewrite: backup original, write temp file, replace ────────────
    backup = output.with_suffix(output.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(output, backup)
        logger.info(f"Backup of the original saved to {backup}")

    tmp = output.with_suffix(output.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        for line in header_lines:
            f.write(line if line.endswith("\n") else line + "\n")
        for _, line in rows:
            f.write(line if line.endswith("\n") else line + "\n")
    os.replace(tmp, output)

    print(f"\nDone. Wrote {len(new_bars)} new bar(s) into {output} "
          f"({len(rows)} data rows total).")


if __name__ == "__main__":
    main()
