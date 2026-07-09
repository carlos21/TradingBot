#!/usr/bin/env bash
# Fetch / update MNQ Futures 1m bar data.
# Configuration lives in .env — no arguments needed.
#
# Usage:
#   ./bin/fetch_data.sh           # normal run
#   ./bin/fetch_data.sh -v        # verbose (DEBUG logging)
#
# To run daily via cron (18:00 Chicago time, Mon–Fri):
#   0 18 * * 1-5  cd /path/to/TradingBot && ./bin/fetch_data.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# The CSV lives at the project root, not under backend/.
CSV_DIR="../csvs"
mkdir -p "$CSV_DIR"

# fetcher is a module under backend/
cd backend
FETCH_OUTPUT="$CSV_DIR/NQ_live.csv" poetry run python -m fetcher.run "$@"
