#!/usr/bin/env bash
# Compare CFD vs Real Futures side-by-side.
# Generates scenarios_out/mode_comparison.html

set -euo pipefail

cd "$(dirname "$0")/.."

source "bin/_scenario_group.sh"

parse_scenario_args "$@"
YAML="$(resolve_scenario_yaml "$GROUP")"

poetry run python backend/scripts/compare_modes.py \
  --yaml "$YAML" \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --quiet \
  ${FILTERED_ARGS[@]+"${FILTERED_ARGS[@]}"}
# Groups: --group ny (default) | --group london | --group path/to/file.yaml
