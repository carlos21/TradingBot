from datetime import datetime
import os

from app_factory import create_app, Repositories
from src.repositories.lines_repository import SQLLineRepository
from src.repositories.trades_repository import SQLTradeRepository
from src.data_sources.csv_datasource import CSVDataSource
from src.database import database

# Import the centralized configuration
from src.prod_config import (
    get_prod_strategy_numbers,
    get_prod_candle_config,
    get_prod_strategy_options
)

PAIR = "NQ"

def build_prod():
    # 1. DB & Repositories (Real SQL for Production)
    database.setup_database()
    repos = Repositories(
        lines = SQLLineRepository(),
        trades= SQLTradeRepository(),
    )

    # 2. Configuration (Env Vars)
    csv_file = os.environ.get("CSV_FILE")
    start_iso = os.environ.get("START_ISO")
    end_iso   = os.environ.get("END_ISO")
    bps       = float(os.environ.get("BARS_PER_SECOND", 10.0))

    initial_start = datetime.fromisoformat(start_iso) if start_iso else datetime.fromisoformat("2023-01-01T00:00:00+00:00")
    initial_end   = datetime.fromisoformat(end_iso) if end_iso else datetime.fromisoformat("2023-04-01T08:20:00+00:00")

    # 3. Data Source
    ds = CSVDataSource(
        pair=PAIR,
        filename=csv_file,
        initial_start_time=initial_start,
        initial_end_time=initial_end,
        bars_per_second=bps,
    )

    # 4. Strategy Logic (Centralized)
    numbers = get_prod_strategy_numbers()
    candle_config = get_prod_candle_config()
    options = get_prod_strategy_options(numbers.max_bounce)

    # 5. Build App
    return create_app(
        pair=PAIR,
        data_source=ds,
        repos=repos,
        numbers=numbers,
        options=options,
        candle_config=candle_config,
        timeframes=["5m", "15m", "30m", "1h"],
        bootstrap_existing_lines=True,
    )

if __name__ == "__main__":
    wiring = build_prod()
    wiring.socketio.run(wiring.app, debug=True, port=5001)