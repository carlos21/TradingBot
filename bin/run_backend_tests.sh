#!/usr/bin/env bash
set -euo pipefail

# Colors
BOLD='\033[1m'
CYAN='\033[96m'
GREEN='\033[92m'
RED='\033[91m'
RST='\033[0m'

echo -e "${BOLD}${CYAN}▶ Running unit tests with coverage…${RST}\n"

if command -v poetry &>/dev/null; then
    if [ $# -eq 0 ]; then
        poetry run coverage run -m pytest -c backend/pytest.ini backend/tests/
    else
        # Allow callers to pass paths either as backend/tests/... or tests/...
        args=()
        for arg in "$@"; do
            if [[ "$arg" == tests/* ]]; then
                args+=("backend/$arg")
            else
                args+=("$arg")
            fi
        done
        poetry run coverage run -m pytest -c backend/pytest.ini "${args[@]}"
    fi
    echo -e "\n${BOLD}${CYAN}▶ Coverage…${RST}\n"
    poetry run coverage report --format=total
else
    echo -e "${RED}Poetry not found. Please install Poetry or activate the virtual environment.${RST}"
    exit 1
fi

echo -e "\n${BOLD}${GREEN}✓ Unit tests finished${RST}"
