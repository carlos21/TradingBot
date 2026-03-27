#!/bin/bash
# E2E Test Runner — interactive script for testing Python <-> NinjaTrader pipeline
# Requires: curl, python3 (for JSON parsing)
# Usage: ./bin/e2e_test.sh

set -euo pipefail

BASE_URL="${E2E_URL:-http://localhost:5001}"

# Colors
BOLD='\033[1m'
CYAN='\033[96m'
GREEN='\033[92m'
RED='\033[91m'
YELLOW='\033[93m'
GRAY='\033[90m'
RST='\033[0m'

parse_json() {
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)" 2>/dev/null
}

test_connection() {
    echo -e "${CYAN}Testing connection to ${BASE_URL}...${RST}"
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE_URL}/api/nt/test_connection" \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null || echo "000")
    if [ "$http_code" = "200" ]; then
        echo -e "${GREEN}${BOLD}Connection OK${RST} (HTTP 200)"
        return 0
    else
        echo -e "${RED}${BOLD}Connection FAILED${RST} (HTTP ${http_code})"
        echo -e "${GRAY}Make sure the app is running: MODE=live poetry run python app.py${RST}"
        return 1
    fi
}

run_scenario() {
    local scenario=$1
    echo ""
    echo -e "${CYAN}${BOLD}Running scenario: ${scenario}${RST}"

    local resp
    resp=$(curl -s -X POST "${BASE_URL}/api/nt/run_e2e_test" \
        -H 'Content-Type: application/json' \
        -d "{\"scenario\":\"${scenario}\"}")

    local trade_id
    trade_id=$(echo "$resp" | parse_json "['trade_id']")
    local entry
    entry=$(echo "$resp" | parse_json "['entry_price']")

    if [ -z "$trade_id" ]; then
        echo -e "${RED}  Failed to start test (no trade_id in response)${RST}"
        echo "  Response: $resp"
        return 1
    fi

    echo -e "  Trade ID:    ${BOLD}${trade_id}${RST}"
    echo -e "  Entry Price: ${entry}"
    echo -e "${GRAY}  Waiting for NinjaTrader to process commands...${RST}"

    # Poll for result — NinjaTrader needs time to go through the command sequence
    local max_wait=15
    local waited=0
    local passed="False"

    while [ $waited -lt $max_wait ]; do
        sleep 1
        waited=$((waited + 1))

        local result
        result=$(curl -s "${BASE_URL}/api/nt/test_result/${trade_id}" 2>/dev/null || echo "{}")
        passed=$(echo "$result" | parse_json "['passed']" 2>/dev/null || echo "")

        if [ "$passed" = "True" ]; then
            echo -e "  ${GREEN}${BOLD}${scenario}: PASSED${RST} (${waited}s)"
            return 0
        fi

        # Check if trade is closed (CLOSE event present = test is done, check pass/fail)
        local events
        events=$(echo "$result" | parse_json "['events_found']" 2>/dev/null || echo "")
        if echo "$events" | grep -q "'CLOSE'"; then
            # Trade closed but didn't pass — show logs
            echo -e "  ${RED}${BOLD}${scenario}: FAILED${RST} (${waited}s)"
            echo ""
            echo -e "${GRAY}--- Trade Logs ---${RST}"
            echo "$result" | parse_json "['formatted_logs']"
            echo -e "${GRAY}--- Events ---${RST}"
            echo "$events"
            return 1
        fi
    done

    echo -e "  ${YELLOW}${scenario}: TIMEOUT${RST} (waited ${max_wait}s)"
    echo -e "  ${GRAY}Is NinjaTrader connected and processing commands?${RST}"
    echo -e "  ${GRAY}Check result manually: curl ${BASE_URL}/api/nt/test_result/${trade_id}${RST}"
    return 1
}

check_result() {
    echo ""
    read -p "Trade ID: " tid
    if [ -z "$tid" ]; then
        echo "No trade ID provided."
        return
    fi

    local result
    result=$(curl -s "${BASE_URL}/api/nt/test_result/${tid}" 2>/dev/null || echo "{}")
    local passed
    passed=$(echo "$result" | parse_json "['passed']" 2>/dev/null || echo "unknown")

    echo ""
    if [ "$passed" = "True" ]; then
        echo -e "${GREEN}${BOLD}Result: PASSED${RST}"
    elif [ "$passed" = "False" ]; then
        echo -e "${RED}${BOLD}Result: FAILED${RST}"
    else
        echo -e "${YELLOW}Result: unknown (trade not found?)${RST}"
    fi

    echo ""
    echo -e "${GRAY}--- Trade Logs ---${RST}"
    echo "$result" | parse_json "['formatted_logs']" 2>/dev/null || echo "(no logs)"
    echo ""
    echo -e "${GRAY}--- Events ---${RST}"
    echo "$result" | parse_json "['events_found']" 2>/dev/null || echo "(no events)"
}

run_all() {
    echo -e "${BOLD}${CYAN}Running all 3 E2E test scenarios...${RST}"

    local pass_count=0
    local total=3

    for scenario in tp_hit sl_hit session_end; do
        if run_scenario "$scenario"; then
            pass_count=$((pass_count + 1))
        fi
    done

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if [ $pass_count -eq $total ]; then
        echo -e "${GREEN}${BOLD}  ALL PASSED: ${pass_count}/${total}${RST}"
    else
        echo -e "${RED}${BOLD}  ${pass_count}/${total} PASSED${RST}"
    fi
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

show_menu() {
    echo ""
    echo -e "${BOLD}${CYAN}  E2E Test Runner${RST}"
    echo -e "${GRAY}  Server: ${BASE_URL}${RST}"
    echo ""
    echo "  1) Run ALL scenarios (tp_hit + sl_hit + session_end)  [requires NinjaTrader]"
    echo "  2) Run single scenario                                [requires NinjaTrader]"
    echo "  3) Check test result (by trade_id)                    [Python only]"
    echo "  4) Test connection (ping Python)                      [Python only]"
    echo "  5) Quit"
    echo ""
}

# Main loop
while true; do
    show_menu
    read -p "  Choose [1-5]: " choice

    case "$choice" in
        1)
            run_all
            ;;
        2)
            echo ""
            echo "  Scenarios: tp_hit, sl_hit, session_end"
            read -p "  Scenario: " sc
            if [ -z "$sc" ]; then
                echo "No scenario provided."
            elif echo "$sc" | grep -qE '^(tp_hit|sl_hit|session_end)$'; then
                run_scenario "$sc"
            else
                echo -e "${RED}Invalid scenario. Must be: tp_hit, sl_hit, or session_end${RST}"
            fi
            ;;
        3)
            check_result
            ;;
        4)
            test_connection
            ;;
        5|q|Q)
            echo "Bye."
            exit 0
            ;;
        *)
            echo "Invalid choice."
            ;;
    esac

    echo ""
    echo -e "${GRAY}Press Enter to continue...${RST}"
    read -r
done
