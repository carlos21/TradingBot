#!/usr/bin/env bash
set -euo pipefail

# Launcher for MetaTrader instance.
# Uses ZMQ port range 5565-5568 to avoid conflict with NinjaTrader (5555-5558).
#
# Usage:
#   ./bin/start_mt.sh
#
# Or override any variable:
#   PAIR=EURUSD ./bin/start_mt.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export MODE="live"
export INSTANCE_NAME="${INSTANCE_NAME:-meta}"
export PLATFORM_TYPE="${PLATFORM_TYPE:-metatrader}"
export PAIR="${PAIR:-EURUSD}"

# Isolation: separate DB, logs, and Flask port
export DB_PATH="${DB_PATH:-sqlite:///./meta.db}"
export LOG_DIR="${LOG_DIR:-logs/meta}"
export FLASK_PORT="${FLASK_PORT:-5002}"

# ZMQ ports offset by +10 to avoid collision with NinjaTrader defaults
export ZMQ_HOST="${ZMQ_HOST:-127.0.0.1}"
export ZMQ_MARKET_PORT="${ZMQ_MARKET_PORT:-5565}"
export ZMQ_COMMAND_PORT="${ZMQ_COMMAND_PORT:-5566}"
export ZMQ_QUERY_PORT="${ZMQ_QUERY_PORT:-5567}"
export ZMQ_HEARTBEAT_PORT="${ZMQ_HEARTBEAT_PORT:-5568}"

cd "$PROJECT_DIR"

echo "[${INSTANCE_NAME}] Starting live — pair=$PAIR"
echo "[${INSTANCE_NAME}] DB: $DB_PATH | Logs: $LOG_DIR | Flask port: $FLASK_PORT"
echo "[${INSTANCE_NAME}] ZMQ: $ZMQ_MARKET_PORT/$ZMQ_COMMAND_PORT/$ZMQ_QUERY_PORT/$ZMQ_HEARTBEAT_PORT"

exec poetry run python app.py
