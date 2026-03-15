poetry run python scripts/run_scenarios.py \
  --yaml tests/test_scenario.yaml \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --chart-selector "#chartContainer" \
  --quiet \
  --decision-log \
  "  "$@"
# Modes: --mode sim | --mode real_futures | --mode real_cfd | --mode both
# CFD: --cfd-spread 0.5 (points, default) --cfd-commission 5.0 (USD, default)"