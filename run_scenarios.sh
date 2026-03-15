poetry run python scripts/run_scenarios.py \
  --yaml tests/scenarios.yaml \
  --source-csv csvs/NQ_live.csv \
  --outdir ./scenarios_out \
  --port 5001 \
  --bars-per-second 800 \
  --chart-selector "#chartContainer" \
  --quiet \
  "$@"
# Modes: --mode sim (fixed risk) | --mode real_futures (MNQ) | --mode real_cfd (CFD) | --mode both (all 3)
# CFD options: --cfd-spread 0.5 (default) --cfd-commission 5.0 (default)
# Example: ./run_scenarios.sh --mode both --cfd-spread 1.0
# To generate an HTML report with monthly view, add: --html-report