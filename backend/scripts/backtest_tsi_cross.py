"""Backtest the TSI Cross strategy on the last 3 months of NQ_live.csv.

Usage:
    python scripts/backtest_tsi_cross.py

This sets the strategy to ``tsi_cross`` and runs on NQ_live.csv from
2026-02-22 to 2026-05-22.
"""

import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app import main

if __name__ == "__main__":
    # Override sys.argv so the CLI loader picks up our desired settings
    sys.argv = [
        "app.py",
        "--strategy-name", "tsi_cross",
        "--csv-file", "csvs/NQ_live.csv",
        "--start", "2026-02-22 17:00:00",
        "--end", "2026-05-22 00:00:00",
        "--pair", "MNQ",
        "--bars-per-second", "1000",
    ]
    main()
