poetry run python scripts/run_scenarios.py \
  --yaml tests/scenarios.yaml \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --chart-selector "#chartContainer"