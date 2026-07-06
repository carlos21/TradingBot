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
DB_FILE="${PROJECT_DIR}/database.db"

export MODE="live"
export PAIR="${PAIR:-MNQ}"

cd "$PROJECT_DIR"

# Kill any existing Liquid process to free ZMQ ports & DB locks
echo "[liquid] Checking for existing processes..."

# 1. Try to kill from PIDFILE first (graceful)
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[liquid] Killing existing process from PIDFILE: $OLD_PID"
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
        if kill -0 "$OLD_PID" 2>/dev/null; then
            kill -9 "$OLD_PID" 2>/dev/null || true
        fi
    fi
    rm -f "$PIDFILE"
fi

# 2. Aggressively kill any leftover python backend/run.py processes
STALE_PIDS=$(pgrep -f "python.*backend/run\.py" || true)
if [ -n "$STALE_PIDS" ]; then
    echo "[liquid] Killing stale python processes: $STALE_PIDS"
    echo "$STALE_PIDS" | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# 3. Kill any process holding the database lock
if command -v fuser >/dev/null 2>&1 && [ -f "$DB_FILE" ]; then
    DB_PIDS=$(fuser "$DB_FILE" 2>/dev/null || true)
    if [ -n "$DB_PIDS" ]; then
        echo "[liquid] Killing processes holding database lock: $DB_PIDS"
        echo "$DB_PIDS" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
fi

# 4. Clean up stale WAL/SHM files so SQLite can re-open cleanly
rm -f "${DB_FILE}-shm" "${DB_FILE}-wal" "${DB_FILE}-journal"

echo "[liquid] Starting live — pair=$PAIR"
# Write our PID before exec (exec keeps the same PID)
echo $$ > "$PIDFILE"
exec poetry run python backend/run.py
