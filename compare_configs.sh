poetry run python scripts/compare_configs.py \
  --yaml tests/scenarios.yaml \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --chart-selector "#chartContainer" \
  --no-snapshot \
  "$@"
# Usage: ./compare_configs.sh --rr 3.3 4.0 --account 100000 --risk 1000 --no-breakeven
# Compare 3+ configs: ./compare_configs.sh --rr 3.3 4.0 5.0 --account 100000 --risk 1000
