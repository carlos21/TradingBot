#!/usr/bin/env bash
set -euo pipefail

# ── ANSI ──
BOLD='\033[1m'
CYAN='\033[96m'
GREEN='\033[92m'
GRAY='\033[90m'
RST='\033[0m'

# ── Arrow-key selector ──
select_option() {
  local -n _result=$1; shift
  local prompt="$1"; shift
  local opts=("$@")
  local sel=0 total=${#opts[@]}

  tput civis
  while true; do
    echo -e "\n${BOLD}${CYAN}  ${prompt}${RST}" >&2
    for i in "${!opts[@]}"; do
      if [ "$i" -eq "$sel" ]; then
        echo -e "  ${GREEN}${BOLD}▶  ${opts[$i]}${RST}" >&2
      else
        echo -e "  ${GRAY}   ${opts[$i]}${RST}" >&2
      fi
    done

    IFS= read -rsn3 key
    case "$key" in
      $'\x1b[A') (( sel = (sel - 1 + total) % total )) ;;
      $'\x1b[B') (( sel = (sel + 1) % total )) ;;
      '')  break ;;
    esac

    for (( i=0; i<total+2; i++ )); do tput cuu1; tput el; done
  done
  tput cnorm
  _result="${opts[$sel]}"
}

# ── Text input with default ──
read_input() {
  local -n _result=$1
  local prompt="$2"
  local default="$3"
  echo -en "  ${BOLD}${prompt}${RST} ${GRAY}[${default}]${RST}: " >&2
  read -r val
  _result="${val:-$default}"
}

# ──────────────────────────────────────────────
clear
echo -e "${BOLD}${CYAN}  TradingBot — Start Server${RST}"
echo -e "${GRAY}  Use ↑ ↓ to select, Enter to confirm${RST}"

trap 'tput cnorm' EXIT

# 1. Mode
select_option MODE "Mode:" "backtest" "live"

if [ "$MODE" = "live" ]; then
  echo ""
  read_input PAIR "Pair" "MNQ"
  read_input NT_ACCOUNT "NinjaTrader account" ""
  echo ""
  echo -e "${GREEN}${BOLD}  Starting live server (${PAIR})...${RST}"
  echo ""
  MODE=live PAIR="$PAIR" NT_ACCOUNT="$NT_ACCOUNT" exec poetry run python app.py
fi

# Backtest — no prompts needed, just start
echo ""
echo -e "${GREEN}${BOLD}  Starting backtest server...${RST}"
echo ""
exec poetry run python app.py
