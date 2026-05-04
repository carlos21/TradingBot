#!/usr/bin/env bash
set -euo pipefail

# Non-interactive launcher for live trading.
# Designed to be called by the systemd service or run manually.
#
# Override defaults via environment variables:
#   PAIR=MNQ NT_ACCOUNTS=MyAccount RISK=100 RR_RATIO=5.0 ./bin/start_live.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export MODE="live"
export PAIR="${PAIR:-MNQ}"
export NT_ACCOUNTS="${NT_ACCOUNTS:-FNFTCHCARLOSDUCLOS42006}"
export RISK="${RISK:-160}"
export RISK_PCT="${RISK_PCT:-}"
export RR_RATIO="${RR_RATIO:-5.0}"

cd "$PROJECT_DIR"

# Kill any existing TradingBot process to free ZMQ ports
echo "[tradingbot] Checking for existing processes..."
EXISTING_PIDS=$(pgrep -f "python app.py" || true)
if [ -n "$EXISTING_PIDS" ]; then
    echo "[tradingbot] Killing existing process(es): $EXISTING_PIDS"
    kill $EXISTING_PIDS 2>/dev/null || true
    sleep 2
    # Force kill if still running
    for pid in $EXISTING_PIDS; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
fi

echo "[tradingbot] Starting live — pair=$PAIR accounts=$NT_ACCOUNTS rr=$RR_RATIO"
exec poetry run python app.py