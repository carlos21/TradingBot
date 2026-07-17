#!/usr/bin/env bash
set -euo pipefail

# Launcher for MetaTrader instance.
# Uses ZMQ port range 5565-5568 to avoid conflict with NinjaTrader (5555-5558).
#
# Usage:
#   ./bin/start_mt.sh
#
# Or override any variable:
#   PAIR=NAS100 ./bin/start_mt.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

export MODE="live"
export INSTANCE_NAME="${INSTANCE_NAME:-meta}"
export PLATFORM_TYPE="${PLATFORM_TYPE:-metatrader}"
export PAIR="${PAIR:-NAS100}"

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
    export DB_PATH="${DB_PATH:-sqlite:///./meta.db}"
fi

export LOG_DIR="${LOG_DIR:-logs/meta}"
export FLASK_PORT="${FLASK_PORT:-5002}"

# ZMQ ports offset by +10 to avoid collision with NinjaTrader defaults
export ZMQ_MARKET_PORT="${ZMQ_MARKET_PORT:-5565}"
export ZMQ_COMMAND_PORT="${ZMQ_COMMAND_PORT:-5566}"
export ZMQ_QUERY_PORT="${ZMQ_QUERY_PORT:-5567}"
export ZMQ_HEARTBEAT_PORT="${ZMQ_HEARTBEAT_PORT:-5568}"

# Auto-detect WSL2 IP for info display.
# Python binds to 0.0.0.0 (all interfaces) so Windows can reach it via
# WSL2 localhost forwarding (127.0.0.1). The EA connects to 127.0.0.1.
if grep -qi microsoft /proc/version 2>/dev/null && [ -f /proc/sys/fs/binfmt_misc/WSLInterop ]; then
    WSL_IP="$(ip route show default | grep -oP 'src \K[\d.]+' || hostname -I | awk '{print $1}')"
    echo "[${INSTANCE_NAME}] Detected WSL2 environment"
    echo "[${INSTANCE_NAME}] WSL2 VM IP: $WSL_IP (for info only)"
    echo "[${INSTANCE_NAME}] Python binds to 0.0.0.0; EA connects to 127.0.0.1"
fi

# Python binds to all interfaces so Windows EA can reach it via localhost
export ZMQ_HOST="${ZMQ_HOST:-0.0.0.0}"

cd "$PROJECT_DIR"

# Generate EA config file so the EA auto-loads the correct WSL2 IP
# Use Python to generate minified JSON (avoids text-mode parsing issues in MQL5)
if command -v python3 &>/dev/null; then
    python3 -c "
import json
cfg = {
    'host': '127.0.0.1',
    'marketPort': $ZMQ_MARKET_PORT,
    'commandPort': $ZMQ_COMMAND_PORT,
    'queryPort': $ZMQ_QUERY_PORT,
    'heartbeatPort': $ZMQ_HEARTBEAT_PORT,
    'pair': '$PAIR',
    'historyDays': 30,
    'autoConnectOnStartup': True,
    'autoShowPanel': True,
    'platformVersion': '2.0.0'
}
with open('TradingBotZmqConfig.json', 'w', encoding='utf-8') as f:
    json.dump(cfg, f, separators=(',', ':'))
"
else
    # Fallback to pure bash (single line, no extra whitespace)
    echo '{"host":"127.0.0.1","marketPort":'$ZMQ_MARKET_PORT',"commandPort":'$ZMQ_COMMAND_PORT',"queryPort":'$ZMQ_QUERY_PORT',"heartbeatPort":'$ZMQ_HEARTBEAT_PORT',"pair":"'$PAIR'","autoConnectOnStartup":true,"autoShowPanel":true,"platformVersion":"2.0.0"}' > TradingBotZmqConfig.json
fi
echo "[${INSTANCE_NAME}] Generated TradingBotZmqConfig.json"

# Auto-copy to MetaTrader MQL5/Files if the directory exists
MT_FILES_DIR="/mnt/c/Users/dark_/AppData/Roaming/MetaQuotes/Terminal/73B7A2420D6397DFF9014A20F1201F97/MQL5/Files"
if [ -d "$MT_FILES_DIR" ]; then
    cp -f "$PROJECT_DIR/TradingBotZmqConfig.json" "$MT_FILES_DIR/TradingBotZmqConfig.json"
    echo "[${INSTANCE_NAME}] Copied config to MetaTrader MQL5/Files ✓"
else
    # Try to auto-detect any Terminal */MQL5/Files directory
    AUTO_DETECTED=$(find /mnt/c/Users/dark_/AppData/Roaming/MetaQuotes/Terminal -maxdepth 2 -type d -name "Files" 2>/dev/null | head -1)
    if [ -n "$AUTO_DETECTED" ] && [ -d "$AUTO_DETECTED" ]; then
        cp -f "$PROJECT_DIR/TradingBotZmqConfig.json" "$AUTO_DETECTED/TradingBotZmqConfig.json"
        echo "[${INSTANCE_NAME}] Copied config to MetaTrader MQL5/Files ✓"
    else
        echo "[${INSTANCE_NAME}] ⚠ Could not find MetaTrader MQL5/Files folder."
        echo "[${INSTANCE_NAME}] Please copy TradingBotZmqConfig.json manually:"
        echo "[${INSTANCE_NAME}]   From: $PROJECT_DIR/TradingBotZmqConfig.json"
        echo "[${INSTANCE_NAME}]   To:   C:\\Users\\dark_\\AppData\\Roaming\\MetaQuotes\\Terminal\\<hash>\\MQL5\\Files\\"
    fi
fi

mask_db_url() {
    local url="$1"
    # Mask password in URLs like postgresql://user:pass@host/db
    echo "$url" | sed -E 's#(:)[^:@]+(@)#:***@#'
}

if [ -n "${DATABASE_URL:-}" ]; then
    DB_DISPLAY="$(mask_db_url "$DATABASE_URL")"
else
    DB_DISPLAY="${DB_PATH:-sqlite:///./meta.db}"
fi

echo "[${INSTANCE_NAME}] Starting live — pair=$PAIR"
echo "[${INSTANCE_NAME}] DB: $DB_DISPLAY | Logs: $LOG_DIR | Flask port: $FLASK_PORT"
echo "[${INSTANCE_NAME}] ZMQ: Python binds 0.0.0.0:$ZMQ_MARKET_PORT/$ZMQ_COMMAND_PORT/$ZMQ_QUERY_PORT/$ZMQ_HEARTBEAT_PORT"
echo "[${INSTANCE_NAME}] ZMQ: EA connects 127.0.0.1:$ZMQ_MARKET_PORT/$ZMQ_COMMAND_PORT/$ZMQ_QUERY_PORT/$ZMQ_HEARTBEAT_PORT"

exec poetry run python backend/run.py
