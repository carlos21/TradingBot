#!/usr/bin/env bash
set -euo pipefail

# Non-interactive launcher for live trading.
# Designed to be called by the systemd service or run manually.
#
# Override defaults via environment variables:
#   PAIR=NQ NT_ACCOUNT=MyAccount RISK=100 RR_RATIO=5.0 ./bin/start_live.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export MODE="live"
export PAIR="${PAIR:-MNQ}"
export NT_ACCOUNT="${NT_ACCOUNT:-FNFTCHCARLOSDUCLOS42006}"
export RISK="${RISK:-160}"
export RISK_PCT="${RISK_PCT:-}"
export RR_RATIO="${RR_RATIO:-5.0}"

cd "$PROJECT_DIR"

echo "[tradingbot] Starting live — pair=$PAIR account=$NT_ACCOUNT rr=$RR_RATIO"
exec poetry run python app.py
