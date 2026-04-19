#!/usr/bin/env bash
set -euo pipefail

# Convenience launcher that starts BOTH NinjaTrader and MetaTrader instances
# in the background. Each instance is fully isolated (separate DB, logs, ports).
#
# Usage:
#   ./bin/start_multi.sh
#
# To stop both:
#   ./bin/start_multi.sh stop

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PIDFILE="${PROJECT_DIR}/.multi_instance_pids"

start_instances() {
    echo "[multi] Starting NinjaTrader instance..."
    nohup "${SCRIPT_DIR}/start_nt.sh" > "${PROJECT_DIR}/logs/ninja.out" 2>&1 &
    NT_PID=$!

    echo "[multi] Starting MetaTrader instance..."
    nohup "${SCRIPT_DIR}/start_mt.sh" > "${PROJECT_DIR}/logs/meta.out" 2>&1 &
    MT_PID=$!

    mkdir -p "${PROJECT_DIR}/logs"
    echo "${NT_PID}" > "$PIDFILE"
    echo "${MT_PID}" >> "$PIDFILE"

    echo "[multi] Both instances started."
    echo "[multi] NinjaTrader PID: $NT_PID  (logs: logs/ninja.out)"
    echo "[multi] MetaTrader   PID: $MT_PID  (logs: logs/meta.out)"
    echo "[multi] Web UIs: http://localhost:5001 (NT) and http://localhost:5002 (MT)"
}

stop_instances() {
    if [[ ! -f "$PIDFILE" ]]; then
        echo "[multi] No PID file found. Are the instances running?"
        exit 1
    fi

    while read -r pid; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "[multi] Stopping PID $pid..."
            kill "$pid" 2>/dev/null || true
        fi
    done < "$PIDFILE"

    rm -f "$PIDFILE"
    echo "[multi] Both instances stopped."
}

case "${1:-}" in
    stop)
        stop_instances
        ;;
    *)
        mkdir -p "${PROJECT_DIR}/logs/ninja" "${PROJECT_DIR}/logs/meta"
        start_instances
        ;;
esac
