#!/usr/bin/env bash
set -euo pipefail

# Run the MQL5 unit tests (Tests/TestRunnerEA.mq5) in the MetaTrader 5
# Strategy Tester, from WSL.
#
# Flow:
#   1. Compile TestRunnerEA.mq5 via bin/compile_metatrader.sh (full compile —
#      the tester needs the .ex5).
#   2. Clear any stale result file from Terminal\Common\Files.
#   3. Write a [Tester] ini into the terminal data dir (Windows-local path —
#      a UNC \\wsl.localhost path was accepted by the terminal but the tester
#      never engaged) and launch terminal64.exe /config:<ini>.
#   4. Poll for MQL_TESTS_RESULT.txt; the EA calls TesterStop() in OnInit and
#      the ini has ShutdownTerminal=1, so the terminal exits by itself.
#   5. Parse the result: exit 0 iff "MQLTESTS: <p> passed, 0 failed".
#
# The ini uses Model=3 ("Math calculations"): no tick history is needed, so
# the run does not stall on trade-server history sync (the suites execute
# entirely in OnInit, ticks are irrelevant).
#
# LIMITATION: a running terminal64 ignores /config. The script refuses to
# start (and never kills) a terminal that was already running — close it
# first or attach TradingBotZmq/Tests/TestRunnerEA to a chart manually.
#
# Env overrides:
#   MT_TERMINAL_EXE     path to terminal64.exe
#   MT_DATA_DIR         terminal data dir (the long-hash one)
#   MT_TEST_SYMBOL      tester symbol (default EURUSD; irrelevant with Model=3)
#   MT_TEST_MODEL       tester model (default 3 = math calculations)
#   MT_TEST_TIMEOUT_SEC max wait for the result file (default 180)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TERMINAL64="${MT_TERMINAL_EXE:-/mnt/c/Program Files/Pepperstone MetaTrader 5/terminal64.exe}"
DATA_DIR="${MT_DATA_DIR:-/mnt/c/Users/dark_/AppData/Roaming/MetaQuotes/Terminal/73B7A2420D6397DFF9014A20F1201F97}"
COMMON_FILES="$(dirname "$DATA_DIR")/Common/Files"
SYMBOL="${MT_TEST_SYMBOL:-EURUSD}"
MODEL="${MT_TEST_MODEL:-3}"
TIMEOUT="${MT_TEST_TIMEOUT_SEC:-180}"
RESULT_FILE="$COMMON_FILES/MQL_TESTS_RESULT.txt"
EXPERT_REL="TradingBotZmq\\Tests\\TestRunnerEA"

fail() { echo "✗ $*" >&2; exit 1; }

# Count running terminal64/metatester64 processes (0 when none). tasklist's
# /FI filter is unreliable through WSL interop; use powershell Get-Process.
mt_process_count() {
    powershell.exe -NoProfile -Command \
        "(Get-Process terminal64,metatester64 -ErrorAction SilentlyContinue).Count" \
        2>/dev/null | tr -d '\r' | grep -oE '^[0-9]+$' || echo 0
}

# Print the tail of the most recent tester + terminal logs (UTF-16LE).
dump_logs() {
    echo "── tester/terminal log tails ─────────────────────────" >&2
    local log
    log="$DATA_DIR/Tester/logs/$(date +%Y%m%d).log"
    if [[ -f "$log" ]]; then
        echo ">>> $log" >&2
        iconv -f UTF-16LE -t UTF-8 "$log" 2>/dev/null | tr -d '\r' | tail -30 >&2
    fi
    log="$(ls -t "$DATA_DIR"/Tester/Agent-*/logs/*.log 2>/dev/null | head -1 || true)"
    if [[ -n "$log" ]]; then
        echo ">>> $log" >&2
        iconv -f UTF-16LE -t UTF-8 "$log" 2>/dev/null | tr -d '\r' | tail -30 >&2
    fi
    log="$DATA_DIR/logs/$(date +%Y%m%d).log"
    if [[ -f "$log" ]]; then
        echo ">>> $log" >&2
        iconv -f UTF-16LE -t UTF-8 "$log" 2>/dev/null | tr -d '\r' | tail -15 >&2
    fi
    echo "──────────────────────────────────────────────────────" >&2
}

command -v wslpath &>/dev/null || fail "wslpath not found — run inside WSL."
[[ -f "$TERMINAL64" ]] || fail "terminal64.exe not found at: $TERMINAL64 (set MT_TERMINAL_EXE)."
[[ -d "$COMMON_FILES" ]] || fail "Common/Files not found at: $COMMON_FILES (set MT_DATA_DIR)."

# --- 0. Refuse to disturb an already-running terminal -------------------
if [[ "$(mt_process_count)" != "0" ]]; then
    echo "✗ terminal64.exe is already running." >&2
    echo "  MetaTrader ignores /config while an instance is up, and this script" >&2
    echo "  will not kill a terminal it did not start. Close the terminal first," >&2
    echo "  or run the tests manually: attach TradingBotZmq/Tests/TestRunnerEA" >&2
    echo "  to any chart — results land in $RESULT_FILE" >&2
    exit 1
fi

# --- 1. Compile -----------------------------------------------------------
echo "▶ Compiling TestRunnerEA..."
MQL_SOURCE="$REPO_ROOT/zmq_connectors/metatrader/Tests/TestRunnerEA.mq5" \
    "$SCRIPT_DIR/compile_metatrader.sh"

# --- 2. Clear stale result -------------------------------------------------
rm -f "$RESULT_FILE"

# --- 3. Generate tester ini -------------------------------------------------
# Written into the terminal data dir: the file must be a Windows-local path
# (CRLF line endings — an LF-only ini is parsed as one garbage line and the
# [Tester] section is silently ignored).
FROM_DATE="$(date -d '5 days ago' +%Y.%m.%d)"
TO_DATE="$(date +%Y.%m.%d)"
INI_FILE="$DATA_DIR/mql_tests_tester.ini"
printf '[Tester]\r\n'                                        >  "$INI_FILE"
printf 'Expert=%s\r\n' "$EXPERT_REL"                        >> "$INI_FILE"
printf 'Symbol=%s\r\n' "$SYMBOL"                            >> "$INI_FILE"
printf 'Period=M1\r\n'                                      >> "$INI_FILE"
printf 'Login=123456\r\n'                                   >> "$INI_FILE"
printf 'Deposit=10000\r\n'                                  >> "$INI_FILE"
printf 'Currency=USD\r\n'                                   >> "$INI_FILE"
printf 'Leverage=1:100\r\n'                                 >> "$INI_FILE"
printf 'Model=%s\r\n' "$MODEL"                              >> "$INI_FILE"
printf 'ExecutionMode=0\r\n'                                >> "$INI_FILE"
printf 'Optimization=0\r\n'                                 >> "$INI_FILE"
printf 'FromDate=%s\r\n' "$FROM_DATE"                       >> "$INI_FILE"
printf 'ToDate=%s\r\n' "$TO_DATE"                           >> "$INI_FILE"
printf 'ForwardMode=0\r\n'                                  >> "$INI_FILE"
printf 'UseLocal=1\r\nUseRemote=0\r\nUseCloud=0\r\n'        >> "$INI_FILE"
printf 'Visual=0\r\n'                                       >> "$INI_FILE"
printf 'ShutdownTerminal=1\r\n'                             >> "$INI_FILE"
WIN_INI="$(wslpath -w "$INI_FILE")"

echo "▶ Launching tester ($SYMBOL M1 model=$MODEL, $FROM_DATE → $TO_DATE)..."

# --- 4. Launch + poll -------------------------------------------------------
"$TERMINAL64" /config:"$WIN_INI" &

elapsed=0
while [[ ! -f "$RESULT_FILE" ]]; do
    if (( elapsed >= TIMEOUT )); then
        echo "✗ Timed out after ${TIMEOUT}s waiting for $RESULT_FILE" >&2
        # We verified no terminal was running before launch, so the only
        # instances around are ours — safe to kill.
        powershell.exe -NoProfile -Command \
            "Get-Process terminal64,metatester64 -ErrorAction SilentlyContinue | Stop-Process -Force" \
            >/dev/null 2>&1 || true
        dump_logs
        exit 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done

# Give the terminal a moment to shut itself down (ShutdownTerminal=1).
sleep 2

# --- 5. Parse result --------------------------------------------------------
echo "──────────────────────────────────────────────"
cat "$RESULT_FILE"
echo "──────────────────────────────────────────────"

SUMMARY="$(grep -E '^MQLTESTS:' "$RESULT_FILE" | head -1 || true)"
[[ -n "$SUMMARY" ]] || fail "Result file has no MQLTESTS: line."

grep -E '^FAIL: ' "$RESULT_FILE" || true

FAILED="$(echo "$SUMMARY" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+')"
if [[ "$FAILED" == "0" ]]; then
    echo "✓ $SUMMARY"
    exit 0
else
    echo "✗ $SUMMARY" >&2
    exit 1
fi
