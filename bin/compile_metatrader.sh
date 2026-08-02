#!/usr/bin/env bash
set -euo pipefail

# Compile the MetaTrader MQL5 connector from the terminal (WSL).
#
# Invokes the Windows MetaEditor64.exe compiler via WSL interop and parses
# its log. MetaEditor's exit code is ALWAYS 0 — errors are detected only by
# parsing the (UTF-16LE) log file.
#
# Usage:
#   bin/compile_metatrader.sh           full compile (emits TradingBotZmqEA.ex5)
#   bin/compile_metatrader.sh --check   syntax check only (/s, no .ex5 emitted)
#
# Env overrides:
#   METAEDITOR_PATH   path to MetaEditor64.exe (default: Pepperstone install)
#   MQL_SOURCE        path to the .mq5 to compile (default: repo EA)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

METAEDITOR="${METAEDITOR_PATH:-/mnt/c/Program Files/Pepperstone MetaTrader 5/MetaEditor64.exe}"
SOURCE="${MQL_SOURCE:-$REPO_ROOT/zmq_connectors/metatrader/TradingBotZmqEA.mq5}"
SYNTAX_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --check) SYNTAX_ONLY=1 ;;
        -h|--help)
            sed -n '2,16p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

if ! command -v wslpath &>/dev/null; then
    echo "wslpath not found. This script is intended to run inside WSL." >&2
    exit 1
fi

if [[ ! -f "$METAEDITOR" ]]; then
    echo "MetaEditor not found at: $METAEDITOR" >&2
    echo "Set METAEDITOR_PATH to your MetaEditor64.exe location." >&2
    exit 1
fi

if [[ ! -f "$SOURCE" ]]; then
    echo "MQL source not found: $SOURCE" >&2
    exit 1
fi

WIN_SOURCE="$(wslpath -w "$SOURCE")"
# MetaEditor writes <SourceName>.log next to the source when /log has no path.
LOG_FILE="${SOURCE%.*}.log"
rm -f "$LOG_FILE"

# MetaEditor resolves <Zmq/...> and <JSON/...> includes against the terminal's
# installed MQL5\Include — NOT the repo's vendor/ copy. Sync vendor includes so
# the compile always reflects the repo (same copies the deploy service makes).
VENDOR_INCLUDE="$REPO_ROOT/zmq_connectors/metatrader/vendor/include"
MT_MQL5_DIR="${MT_MQL5_DIR:-}"
if [[ -z "$MT_MQL5_DIR" ]]; then
    TERMINALS_BASE="/mnt/c/Users/dark_/AppData/Roaming/MetaQuotes/Terminal"
    for candidate in \
        "$TERMINALS_BASE/73B7A2420D6397DFF9014A20F1201F97/MQL5" \
        "$TERMINALS_BASE"/*/MQL5; do
        if [[ -d "$candidate/Include" ]]; then
            MT_MQL5_DIR="$candidate"
            break
        fi
    done
fi
if [[ -n "$MT_MQL5_DIR" && -d "$MT_MQL5_DIR/Include" ]]; then
    cp -rf "$VENDOR_INCLUDE/Zmq" "$MT_MQL5_DIR/Include/" 2>/dev/null \
        && cp -rf "$VENDOR_INCLUDE/JSON" "$MT_MQL5_DIR/Include/" 2>/dev/null \
        && echo "▶ Synced vendor includes → $MT_MQL5_DIR/Include" \
        || echo "⚠ Could not sync vendor includes to $MT_MQL5_DIR/Include" >&2
else
    echo "⚠ MetaTrader MQL5 dir not found — compiling against previously installed includes." >&2
    echo "  Set MT_MQL5_DIR to your terminal's MQL5 folder to enable include sync." >&2
fi

EXTRA_FLAGS=()
if [[ "$SYNTAX_ONLY" -eq 1 ]]; then
    EXTRA_FLAGS+=("/s")
    echo "▶ Syntax-checking: $SOURCE"
else
    echo "▶ Compiling: $SOURCE"
fi

# Exit code is meaningless (always 0); the log is the source of truth.
"$METAEDITOR" /compile:"$WIN_SOURCE" /log "${EXTRA_FLAGS[@]+"${EXTRA_FLAGS[@]}"}" || true

if [[ ! -f "$LOG_FILE" ]]; then
    echo "No log produced at $LOG_FILE — MetaEditor may have failed to start." >&2
    exit 1
fi

# Log is UTF-16LE; convert for display/parsing.
LOG_TEXT="$(iconv -f UTF-16LE -t UTF-8 "$LOG_FILE" | tr -d '\r')"

# Show errors/warnings with their file/line context, then the summary line.
echo "$LOG_TEXT" | grep -iE ':(error|warning) [0-9]+:' || true
# Full compile writes "Result: N errors, ..."; syntax-check (/s) writes
# "information: result N errors ..." (lowercase, no colon) — match both.
RESULT_LINE="$(echo "$LOG_TEXT" | grep -ioE '(information: )?result:? [0-9]+ errors?, [0-9]+ warnings?' || true)"
echo "$RESULT_LINE"

ERRORS="$(echo "$RESULT_LINE" | grep -ioE 'result:? [0-9]+' | grep -oE '[0-9]+' || echo 1)"
if [[ "$ERRORS" != "0" ]]; then
    echo "✗ Compile failed ($ERRORS errors). Full log: $LOG_FILE" >&2
    exit 1
fi

echo "✓ Compile succeeded"
