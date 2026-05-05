#!/usr/bin/env bash
set -euo pipefail

# Non-interactive launcher for live trading.
# Designed to be called by the systemd service or run manually.
#
# Override defaults via environment variables:
#   PAIR=MNQ ./bin/start_live.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PIDFILE="${PROJECT_DIR}/.tradingbot_live.pid"

export MODE="live"
export PAIR="${PAIR:-MNQ}"

cd "$PROJECT_DIR"

# Kill any existing Liquid process to free ZMQ ports
echo "[liquid] Checking for existing processes..."
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[liquid] Killing existing process: $OLD_PID"
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
        if kill -0 "$OLD_PID" 2>/dev/null; then
            kill -9 "$OLD_PID" 2>/dev/null || true
        fi
    fi
    rm -f "$PIDFILE"
fi

echo "[liquid] Starting live — pair=$PAIR"
# Write our PID before exec (exec keeps the same PID)
echo $$ > "$PIDFILE"
exec poetry run python app.py
