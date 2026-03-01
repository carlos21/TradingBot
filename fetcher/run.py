"""
CLI entry point for daily NQ Futures data sync.

Configuration is read from .env in the project root. CLI flags override .env values.

EXAMPLES
--------

  # Simplest — everything comes from .env:
  poetry run python -m fetcher.run

  # Override provider on the fly:
  poetry run python -m fetcher.run --provider polygon

  # Verbose logging:
  poetry run python -m fetcher.run -v

CRON EXAMPLE (runs every weekday at 18:00 Chicago time)
--------------------------------------------------------
  0 18 * * 1-5  cd /path/to/TradingBot && ./fetch_data.sh

USING THE FETCHED CSV IN CSVDataSource
---------------------------------------
  Pass the same FETCH_TZ value as the 'tz' parameter to CSVDataSource:

      ds = CSVDataSource(
          pair="NQ",
          filename="csvs/NQ_live.csv",
          tz="America/Chicago",   # must match FETCH_TZ in .env
      )
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Load .env from project root before anything else
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed — fall back to plain env vars

from fetcher.base import FetchProvider
from fetcher.csv_store import CSVStore
from fetcher.sync import DataSyncer, DEFAULT_LOOKBACK_DAYS


def _build_provider(args: argparse.Namespace) -> FetchProvider:
    if args.provider == "yfinance":
        from fetcher.providers.yfinance_provider import YFinanceProvider
        return YFinanceProvider()

    if args.provider == "polygon":
        from fetcher.providers.polygon_provider import PolygonProvider
        return PolygonProvider(api_key=args.polygon_api_key)

    raise ValueError(f"Unknown provider: {args.provider!r}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m fetcher.run",
        description="Sync NQ Futures 1m bar data to a CSV file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--provider",
        default=os.environ.get("FETCH_PROVIDER", "yfinance"),
        choices=["yfinance", "polygon"],
        help=(
            "Data source to use. "
            "'yfinance' is free but limited to the last 7 days of 1m data. "
            "'polygon' requires POLYGON_API_KEY and supports full history. "
            "[env: FETCH_PROVIDER, default: yfinance]"
        ),
    )
    parser.add_argument(
        "--symbol",
        default=os.environ.get("FETCH_SYMBOL"),
        help=(
            "Provider-specific symbol. "
            "Defaults: 'NQ=F' for yfinance, 'NQ:XCME' for polygon. "
            "[env: FETCH_SYMBOL]"
        ),
    )
    parser.add_argument(
        "--output",
        default=os.environ.get("FETCH_OUTPUT", "csvs/NQ_live.csv"),
        help="Path to the output CSV file. [env: FETCH_OUTPUT, default: csvs/NQ_live.csv]",
    )
    parser.add_argument(
        "--pair",
        default=os.environ.get("FETCH_PAIR", "NQ"),
        help="Pair name embedded in each bar dict. [env: FETCH_PAIR, default: NQ]",
    )
    parser.add_argument(
        "--tz",
        default=os.environ.get("FETCH_TZ", "America/Chicago"),
        help=(
            "Timezone for Date/Time columns in the CSV. "
            "Must match 'tz' in CSVDataSource when reading. "
            "[env: FETCH_TZ, default: America/Chicago]"
        ),
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=int(os.environ.get("FETCH_LOOKBACK", DEFAULT_LOOKBACK_DAYS)),
        metavar="DAYS",
        help=(
            f"Days to look back when the CSV is empty. "
            f"[env: FETCH_LOOKBACK, default: {DEFAULT_LOOKBACK_DAYS}]. "
            "Note: yfinance caps 1m data at 7 days regardless of this value."
        ),
    )
    parser.add_argument(
        "--polygon-api-key",
        default=os.environ.get("POLYGON_API_KEY"),
        metavar="KEY",
        help=(
            "Polygon.io API key. "
            "[env: POLYGON_API_KEY] — only used when --provider polygon."
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logging.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    # Apply default symbol per provider when not set in .env or CLI
    if not args.symbol:
        args.symbol = "NQ=F" if args.provider == "yfinance" else "NQ:XCME"

    provider = _build_provider(args)
    store    = CSVStore(filepath=args.output, pair=args.pair, tz=args.tz)
    syncer   = DataSyncer(provider=provider, store=store)

    print(f"Provider : {provider.name}")
    print(f"Symbol   : {args.symbol}")
    print(f"Output   : {args.output}")
    print(f"CSV TZ   : {args.tz}")
    print(f"Lookback : {args.lookback} days (when CSV is empty)")
    print()

    written = syncer.sync(symbol=args.symbol, lookback_days=args.lookback)

    print()
    if written:
        print(f"Done. Wrote {written} new bar(s) to {args.output}.")
    else:
        print("Done. No new bars to write (CSV is already up to date).")


if __name__ == "__main__":
    main()
