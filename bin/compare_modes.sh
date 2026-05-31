#!/usr/bin/env bash
# Compare CFD vs Real Futures side-by-side.
# Generates scenarios_out/mode_comparison.html

set -euo pipefail

cd "$(dirname "$0")/.."

poetry run python scripts/compare_modes.py \
  --yaml src/strategies/liquidity_v2/scenarios.yaml \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --quiet \
  "$@"
