#!/usr/bin/env bash
set -euo pipefail

# Non-interactive launcher for live mode with sim account.
# Designed to be called by the systemd service or run manually.
#
# Override defaults via environment variables:
#   PAIR=NQ NT_ACCOUNT=Sim101 ./bin/live_sim.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export MODE="live"
export PAIR="${PAIR:-MNQ}"
#export NT_ACCOUNT="${NT_ACCOUNT:-DEMO2020071}"
export NT_ACCOUNT="${NT_ACCOUNT:-FNFTCHCARLOSDUCLOS74105}"
export RISK="${RISK:-50}"
export RISK_PCT="${RISK_PCT:-}"

cd "$PROJECT_DIR"

echo "[tradingbot] Starting live sim — pair=$PAIR account=$NT_ACCOUNT"
exec poetry run python app.py
