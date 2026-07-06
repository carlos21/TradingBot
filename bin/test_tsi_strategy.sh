#!/bin/bash
poetry run python backend/scripts/test_tsi_strategy.py \
  --csv-file csvs/NQ_live.csv \
  --outdir ./tsi_test_out \
  --port 5002 \
  --bars-per-second 800 \
  --snapshot \
  "$@"
