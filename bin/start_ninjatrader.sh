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

# Isolation: separate DB, logs, and Flask port
export DB_PATH="${DB_PATH:-sqlite:///./ninja.db}"
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

echo "[${INSTANCE_NAME}] Starting live — pair=$PAIR"
echo "[${INSTANCE_NAME}] DB: $DB_PATH | Logs: $LOG_DIR | Flask port: $FLASK_PORT"
echo "[${INSTANCE_NAME}] ZMQ: $ZMQ_MARKET_PORT/$ZMQ_COMMAND_PORT/$ZMQ_QUERY_PORT/$ZMQ_HEARTBEAT_PORT"

exec poetry run python backend/run.py
