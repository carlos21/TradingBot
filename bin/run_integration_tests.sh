#!/usr/bin/env bash
poetry run python scripts/run_integration_tests.py \
  --yaml tests/scenarios.yaml \
  --source-csv csvs/NQ_live.csv \
  --port 5002 \
  --bars-per-second 50000 \
  "$@"
