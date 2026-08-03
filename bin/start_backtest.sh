#!/usr/bin/env bash
set -euo pipefail

# Non-interactive launcher for simulation/backtest mode.
# Designed to run the app against historical CSV data.
#
# Usage:
#   ./bin/start_backtest.sh --csv-file csvs/NQ_21-24.csv --start "2024-06-16 00:00:00" --end "2024-06-21 23:59:59"
#
# Or via environment variables (CLI args take precedence):
#   PAIR=MNQ CSV_FILE=csvs/NQ_21-24.csv START_STR="2024-06-16 00:00:00" END_STR="2024-06-21 23:59:59" ./bin/start_backtest.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Defaults (used only when neither CLI arg nor env var is provided)
PAIR="${PAIR:-MNQ}"
CSV_FILE="${CSV_FILE:-csvs/NQ_live.csv}"
BARS_PER_SECOND="${BARS_PER_SECOND:-10.0}"
START_STR="${START_STR:-}"
END_STR="${END_STR:-}"

# Parse CLI args; these override env vars and .env because the app uses
# CompositeConfigLoader where CLI args have highest precedence.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pair)
      PAIR="$2"; shift 2 ;;
    --csv-file)
      CSV_FILE="$2"; shift 2 ;;
    --start)
      START_STR="$2"; shift 2 ;;
    --end)
      END_STR="$2"; shift 2 ;;
    --bars-per-second|--speed)
      BARS_PER_SECOND="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [options]"
      echo "Options:"
      echo "  --pair <symbol>             Trading pair (default: MNQ)"
      echo "  --csv-file <path>           CSV file to replay"
      echo "  --start \"YYYY-MM-DD HH:MM:SS\"  Replay start (input_tz)"
      echo "  --end \"YYYY-MM-DD HH:MM:SS\"    Replay end (input_tz)"
      echo "  --bars-per-second <float>   Replay speed (default: 10)"
      exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1 ;;
  esac
done

export MODE="backtest"
export PAIR
export CSV_FILE
export BARS_PER_SECOND
export START_STR
export END_STR

cd "$PROJECT_DIR"

echo "[liquid] Starting simulation — pair=$PAIR csv=$CSV_FILE range=${START_STR:-*}..${END_STR:-*}"
exec poetry run python backend/run.py
