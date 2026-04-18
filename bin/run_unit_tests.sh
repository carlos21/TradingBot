#!/usr/bin/env bash
set -euo pipefail

# Colors
BOLD='\033[1m'
CYAN='\033[96m'
GREEN='\033[92m'
RED='\033[91m'
RST='\033[0m'

echo -e "${BOLD}${CYAN}▶ Running unit tests…${RST}\n"

if command -v poetry &>/dev/null; then
    poetry run pytest tests/ "$@"
else
    echo -e "${RED}Poetry not found. Please install Poetry or activate the virtual environment.${RST}"
    exit 1
fi

echo -e "\n${BOLD}${GREEN}✓ Unit tests finished${RST}"
