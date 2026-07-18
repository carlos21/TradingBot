#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/_scenario_group.sh"

parse_scenario_args "$@"
YAML="$(resolve_scenario_yaml "$GROUP")"

poetry run python backend/scripts/compare_configs.py \
  --yaml "$YAML" \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --chart-selector "#chartContainer" \
  --no-snapshot \
  ${FILTERED_ARGS[@]+"${FILTERED_ARGS[@]}"}
# Usage: ./bin/compare_configs.sh [--group london] --rr 3.3 4.0 --account 100000 --risk 1000 --no-breakeven
# Compare 3+ configs: ./bin/compare_configs.sh --group london --rr 3.3 4.0 5.0 --account 100000 --risk 1000
