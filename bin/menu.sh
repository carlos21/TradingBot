#!/bin/bash

# ANSI colors
BOLD='\033[1m'
CYAN='\033[96m'
GREEN='\033[92m'
GRAY='\033[90m'
RST='\033[0m'

options=(
  "Run ALL scenarios              (run_scenarios.sh)"
  "Run TEST scenario              (run_test_scenario.sh)"
  "Run integration tests          (run_integration_tests.sh)"
  "Add scenario                   (add_scenario.sh)"
  "Fix scenario                   (fix_scenario.sh)"
  "Fix ALL scenarios              (fix_all_scenarios.sh)"
  "Compare configs                (compare_configs.sh)"
  "Fetch data                     (fetch_data.sh)"
  "Quit"
)

commands=(
  "./bin/run_scenarios.sh"
  "./bin/run_test_scenario.sh"
  "./bin/run_integration_tests.sh"
  "./bin/add_scenario.sh"
  "./bin/fix_scenario.sh"
  "./bin/fix_all_scenarios.sh"
  "./bin/compare_configs.sh"
  "./bin/fetch_data.sh"
  ""
)

selected=0
total=${#options[@]}

draw_menu() {
  clear
  echo -e "${BOLD}${CYAN}  TradingBot — Main Menu${RST}"
  echo -e "${GRAY}  Use ↑ ↓ to navigate, Enter to select${RST}"
  echo ""
  for i in "${!options[@]}"; do
    if [ "$i" -eq "$selected" ]; then
      echo -e "  ${GREEN}${BOLD}▶  ${options[$i]}${RST}"
    else
      echo -e "  ${GRAY}   ${options[$i]}${RST}"
    fi
  done
  echo ""
}

run_selection() {
  local cmd="${commands[$selected]}"
  if [ -z "$cmd" ]; then
    exit 0
  fi
  clear
  echo -e "${BOLD}${CYAN}Running: ${cmd}${RST}\n"
  eval "$cmd"
  echo ""
  echo -e "${GRAY}Press any key to return to menu...${RST}"
  read -rsn1
}

# Hide cursor
tput civis
trap 'tput cnorm; echo ""' EXIT

while true; do
  draw_menu

  # Read a key (handles escape sequences for arrows)
  IFS= read -rsn3 key

  case "$key" in
    $'\x1b[A')  # Up arrow
      (( selected = (selected - 1 + total) % total ))
      ;;
    $'\x1b[B')  # Down arrow
      (( selected = (selected + 1) % total ))
      ;;
    '')  # Enter
      run_selection
      ;;
    q|Q)
      exit 0
      ;;
  esac
done
