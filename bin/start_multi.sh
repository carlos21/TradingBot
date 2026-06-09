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
    mkdir -p "${PROJECT_DIR}/logs"

    if [[ ! -f "${SCRIPT_DIR}/start_nt.sh" ]]; then
        echo "[multi] ERROR: ${SCRIPT_DIR}/start_nt.sh not found. Skipping NinjaTrader."
        NT_PID=""
    else
        echo "[multi] Starting NinjaTrader instance..."
        nohup "${SCRIPT_DIR}/start_nt.sh" > "${PROJECT_DIR}/logs/ninja.out" 2>&1 &
        NT_PID=$!
    fi

    if [[ ! -f "${SCRIPT_DIR}/start_mt.sh" ]]; then
        echo "[multi] ERROR: ${SCRIPT_DIR}/start_mt.sh not found. Skipping MetaTrader."
        MT_PID=""
    else
        echo "[multi] Starting MetaTrader instance..."
        nohup "${SCRIPT_DIR}/start_mt.sh" > "${PROJECT_DIR}/logs/meta.out" 2>&1 &
        MT_PID=$!
    fi

    : > "$PIDFILE"
    [[ -n "$NT_PID" ]] && echo "${NT_PID}" >> "$PIDFILE"
    [[ -n "$MT_PID" ]] && echo "${MT_PID}" >> "$PIDFILE"

    echo "[multi] Instances started."
    [[ -n "$NT_PID" ]] && echo "[multi] NinjaTrader PID: $NT_PID  (logs: logs/ninja.out)"
    [[ -n "$MT_PID" ]] && echo "[multi] MetaTrader   PID: $MT_PID  (logs: logs/meta.out)"
}

stop_instances() {
    if [[ ! -f "$PIDFILE" ]]; then
        echo "[multi] No PID file found. Are the instances running?"
        exit 1
    fi

    while read -r pid; do
        if [[ -z "$pid" ]]; then
            continue
        fi
        # Verify the process is actually one of our Python instances before killing
        if kill -0 "$pid" 2>/dev/null; then
            cmdline="$(cat /proc/${pid}/cmdline 2>/dev/null | tr '\0' ' ' || ps -p "$pid" -o comm= 2>/dev/null || echo "")"
            if [[ "$cmdline" == *"python"* ]] || [[ "$cmdline" == *"start_nt"* ]] || [[ "$cmdline" == *"start_mt"* ]]; then
                echo "[multi] Stopping PID $pid..."
                kill "$pid" 2>/dev/null || true
            else
                echo "[multi] WARNING: PID $pid does not match expected process, skipping."
            fi
        fi
    done < "$PIDFILE"

    rm -f "$PIDFILE"
    echo "[multi] Stopped."
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
