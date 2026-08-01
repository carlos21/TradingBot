#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
poetry run python backend/scripts/compare_slippage.py "$@"
