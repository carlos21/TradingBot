#!/usr/bin/env bash
set -euo pipefail

poetry run python backend/scripts/run_scenarios.py \
  --yaml backend/src/strategies/liquidity_v2/test_scenario.yaml \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --chart-selector "#chartContainer" \
  --quiet \
  --decision-log \
  "$@"
# Modes: --mode sim | --mode real_futures | --mode real_cfd | --mode both
# RR ratio: --rr 4.0 (default) | --rr 2.0 (conservative) | --rr 6.0 (aggressive)
# CFD: --cfd-spread 0.5 (points, default) --cfd-commission 5.0 (USD, default)"