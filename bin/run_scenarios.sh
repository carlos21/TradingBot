#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/_scenario_group.sh"

parse_scenario_args "$@"
YAML="$(resolve_scenario_yaml "$GROUP")"

poetry run python backend/scripts/run_scenarios.py \
  --yaml "$YAML" \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --chart-selector "#chartContainer" \
  --quiet \
  ${FILTERED_ARGS[@]+"${FILTERED_ARGS[@]}"}
# Groups: --group ny (default) | --group london | --group path/to/file.yaml
#         (groups live in backend/src/strategies/liquidity_v2/scenarios/<group>.yaml)
# Modes: --mode sim (fixed risk) | --mode real_futures (MNQ) | --mode real_cfd (CFD) | --mode both (all 3)
# RR ratio: --rr 4.0 (default) | --rr 2.0 (conservative) | --rr 6.0 (aggressive)
# CFD options: --cfd-spread 1.5 (default) --cfd-commission 5.0 (default)
# Example: ./bin/run_scenarios.sh --group london --mode real_cfd --rr 2.0 --cfd-spread 2.0
# To generate an HTML report with monthly view, add: --html-report
