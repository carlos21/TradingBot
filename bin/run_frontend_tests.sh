#!/usr/bin/env bash
set -euo pipefail

# Colors
BOLD='\033[1m'
CYAN='\033[96m'
GREEN='\033[92m'
RED='\033[91m'
RST='\033[0m'

echo -e "${BOLD}${CYAN}▶ Running frontend tests with coverage…${RST}\n"

if ! command -v npm &>/dev/null; then
    echo -e "${RED}npm not found. Please install Node.js/npm.${RST}"
    exit 1
fi

if [ $# -eq 0 ]; then
    npm run test:coverage
else
    npm run test:coverage -- "$@"
fi

echo -e "\n${BOLD}${GREEN}✓ Frontend tests finished${RST}"
