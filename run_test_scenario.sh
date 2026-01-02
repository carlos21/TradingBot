poetry run python scripts/run_scenarios.py \
  --yaml tests/test_scenario.yaml \
  --source-csv csvs/NQ_21-24.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --chart-selector "#chartContainer"