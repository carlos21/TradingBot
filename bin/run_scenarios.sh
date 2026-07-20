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
# Example: ./bin/run_scenarios.sh --group london --mode real_cfd --rr 2.0 --cfd-spread 2.0
# To generate an HTML report with monthly view, add: --html-report
