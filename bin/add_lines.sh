#!/usr/bin/env bash
# Add strategy lines directly to the database (no server required).
# Loops until you press Enter with an empty price or type 'q'.
# Usage: ./bin/add_lines.sh
set -euo pipefail

BOLD='\033[1m'
CYAN='\033[96m'
GREEN='\033[92m'
RED='\033[91m'
GRAY='\033[90m'
RST='\033[0m'

PAIR="${PAIR:-MNQ}"

echo -e "${BOLD}${CYAN}  Add Strategy Lines  ${GRAY}(${PAIR})${RST}"
echo -e "${GRAY}  Empty price or 'q' to quit${RST}"

COUNT=0

while true; do
  echo ""
  read -p "  Price: " PRICE

  if [ -z "$PRICE" ] || [ "$PRICE" = "q" ] || [ "$PRICE" = "Q" ]; then
    break
  fi

  NOW_EPOCH=$(date +%s)
  NOW_HUMAN=$(date -u -d "@$NOW_EPOCH" '+%Y-%m-%d %H:%M:%SZ' 2>/dev/null || date -u -r "$NOW_EPOCH" '+%Y-%m-%d %H:%M:%SZ')

  echo -e "  ${GRAY}Default: now (${NOW_HUMAN})${RST}"
  read -p "  Creation time (epoch or Enter for now): " CREATION_TIME
  CREATION_TIME="${CREATION_TIME:-$NOW_EPOCH}"

  poetry run python3 -c "
import sys
sys.path.insert(0, '.')
from src.database.database import setup_database
from src.repositories.lines_repository import SQLLineRepository
from datetime import datetime, timezone

setup_database()
repo = SQLLineRepository()

creation_date = datetime.fromtimestamp(float('${CREATION_TIME}'), tz=timezone.utc)
line = repo.insert_line(pair='${PAIR}', price=float('${PRICE}'), creation_date=creation_date)

print(f'  ID:    {line.line_id}')
print(f'  Price: {line.price}')
print(f'  Date:  {line.creation_date}')
" && {
    echo -e "  ${GREEN}${BOLD}✓ Added${RST}"
    (( COUNT++ )) || true
  } || {
    echo -e "  ${RED}Failed to add line.${RST}"
  }
done

echo ""
echo -e "${BOLD}  ${COUNT} line(s) added.${RST}"
echo ""
