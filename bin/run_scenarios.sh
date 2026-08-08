#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/_scenario_group.sh"

parse_scenario_args "$@"
resolve_scenario_yamls "$GROUP"

YAML_ARGS=()
for y in "${RESOLVED_YAMLS[@]}"; do
  YAML_ARGS+=(--yaml "$y")
done

poetry run python backend/scripts/run_scenarios.py \
  "${YAML_ARGS[@]}" \
  --group "$GROUP" \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --chart-selector "#chartContainer" \
  --quiet \
  ${FILTERED_ARGS[@]+"${FILTERED_ARGS[@]}"}
# Groups: --group ny (default) | --group london | --group all | --group path/to/file.yaml
#         (groups live in backend/src/strategies/liquidity_v2/scenarios/<group>.yaml;
#          "all" merges every group into one run with trades sorted chronologically
#          so --risk-pct compounding is correct across groups)
# Modes: --mode sim (fixed risk) | --mode real_futures (MNQ) | --mode real_cfd (CFD) | --mode both (all 3)
# RR ratio: --rr 4.0 (default) | --rr 2.0 (conservative) | --rr 6.0 (aggressive)
# CFD options: --cfd-spread 1.5 (default) --cfd-commission 5.0 (default)
# Slippage simulation: --slippage 5 (points) shifts every entry based on the next bar color.
# Tuning knobs: --sl-buffer 5 (extra SL room in pts beyond the sweep extreme; TP distance is
#               NOT widened — measured: raises win rate but lowers long-run PnL)
#               --max-reentry-attempts 2 (default 1; measured: lowers win rate and PnL)
# Every run also prints a POST-SL ANALYSIS (what price did after each stop-out) and a
# PULLBACK ENTRY SIMULATION (limit-at-line vs market-at-cross) after the PnL tables.
# Example: ./bin/run_scenarios.sh --group london --mode real_cfd --rr 2.0 --cfd-spread 2.0
# To generate an HTML report with monthly view, add: --html-report
