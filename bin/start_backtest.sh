#!/usr/bin/env bash
set -euo pipefail

# Non-interactive launcher for simulation/backtest mode.
# Designed to run the app against historical CSV data.
#
# Override defaults via environment variables:
#   PAIR=NQ CSV_FILE=csvs/NQ_live.csv ./bin/start_backtest.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export MODE="backtest"
export PAIR="${PAIR:-MNQ}"
export CSV_FILE="${CSV_FILE:-csvs/NQ_live.csv}"
export BARS_PER_SECOND="${BARS_PER_SECOND:-10.0}"

cd "$PROJECT_DIR"

echo "[tradingbot] Starting simulation — pair=$PAIR csv=$CSV_FILE"
exec poetry run python app.py
