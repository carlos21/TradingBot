#!/usr/bin/env bash
poetry run python scripts/run_integration_tests.py \
  --yaml src/strategies/liquidity_v2/scenarios.yaml \
  --source-csv csvs/NQ_live.csv \
  --port 5002 \
  --bars-per-second 50000 \
  "$@"
