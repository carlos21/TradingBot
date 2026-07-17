#!/usr/bin/env bash
set -euo pipefail

# Launcher for NinjaTrader instance.
# Uses default ZMQ port range 5555-5558 to match the NT ZMQ connector defaults.
#
# Usage:
#   ./bin/start_nt.sh
#
# Or override any variable:
#   PAIR=MNQ ./bin/start_nt.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export MODE="live"
export INSTANCE_NAME="${INSTANCE_NAME:-ninja}"
export PLATFORM_TYPE="${PLATFORM_TYPE:-ninjatrader}"
export PAIR="${PAIR:-MNQ}"

# Load .env so DATABASE_URL is available before Python starts.
# Python's load_dotenv() will not override already-exported env vars,
# so we must read the DB URL here to avoid the shell default hiding it.
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source <(sed '1s/^\xEF\xBB\xBF//' "$PROJECT_DIR/.env")
    set +a
fi

# Prefer PostgreSQL DATABASE_URL; only fall back to SQLite DB_PATH when unset.
if [ -z "${DATABASE_URL:-}" ]; then
    export DB_PATH="${DB_PATH:-sqlite:///./ninja.db}"
fi

export LOG_DIR="${LOG_DIR:-logs/ninja}"
export FLASK_PORT="${FLASK_PORT:-5001}"

# Default ZMQ ports for NinjaTrader
# Bind to 0.0.0.0 so Windows (NinjaTrader) can reach WSL2 via localhost forwarding
export ZMQ_HOST="${ZMQ_HOST:-0.0.0.0}"
export ZMQ_MARKET_PORT="${ZMQ_MARKET_PORT:-5555}"
export ZMQ_COMMAND_PORT="${ZMQ_COMMAND_PORT:-5556}"
export ZMQ_QUERY_PORT="${ZMQ_QUERY_PORT:-5557}"
export ZMQ_HEARTBEAT_PORT="${ZMQ_HEARTBEAT_PORT:-5558}"

cd "$PROJECT_DIR"

mask_db_url() {
    local url="$1"
    # Mask password in URLs like postgresql://user:pass@host/db
    echo "$url" | sed -E 's#(:)[^:@]+(@)#:***@#'
}

if [ -n "${DATABASE_URL:-}" ]; then
    DB_DISPLAY="$(mask_db_url "$DATABASE_URL")"
else
    DB_DISPLAY="${DB_PATH:-sqlite:///./ninja.db}"
fi

echo "[${INSTANCE_NAME}] Starting live — pair=$PAIR"
echo "[${INSTANCE_NAME}] DB: $DB_DISPLAY | Logs: $LOG_DIR | Flask port: $FLASK_PORT"
echo "[${INSTANCE_NAME}] ZMQ: $ZMQ_MARKET_PORT/$ZMQ_COMMAND_PORT/$ZMQ_QUERY_PORT/$ZMQ_HEARTBEAT_PORT"

exec poetry run python backend/run.py
