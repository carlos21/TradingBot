from datetime import datetime
import os
from zoneinfo import ZoneInfo

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

# --- CONFIGURATION TIMEZONES ---
# This controls how the DATES you type below are interpreted.
# We set this to New York so your inputs match the Chart display.
INPUT_TZS = {
    'NQ':     'America/New_York', 
    'ES':     'America/New_York',
    'EURUSD': 'Europe/London',
}

def parse_input_to_epoch(date_str: str, pair: str) -> int:
    """
    Parses a date string (ISO-like) using the INPUT_TZS for the pair,
    then converts it to a UTC epoch timestamp.
    """
    if not date_str:
        return None
        
    # Default to UTC if pair not found
    tz_name = INPUT_TZS.get(pair, "UTC")
    input_tz = ZoneInfo(tz_name)
    
    # Parse string to datetime (naive)
    try:
        dt = datetime.fromisoformat(date_str)
    except ValueError:
        # Fallback for simple space-separated format
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

    # Attach the timezone you expect (New York)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=input_tz)
    
    # Convert to UTC timestamp for the system
    return int(dt.timestamp())

def build_prod():
    # 1. DB & Repositories
    database.setup_database()
    repos = Repositories(
        lines = SQLLineRepository(),
        trades= SQLTradeRepository(),
    )

    # 2. Configuration
    csv_file = os.environ.get("CSV_FILE", "csvs/NQ_21-24.csv")
    bps      = float(os.environ.get("BARS_PER_SECOND", 10.0))

    # --- HARDCODED DATES (New York Time) ---
    # Since INPUT_TZS['NQ'] is America/New_York, these times 
    # will match exactly what you see on the chart.
    start_str = "2024-03-01 08:25:00"
    end_str   = "2024-03-24 08:25:00"

    # Convert Input (NY) -> UTC Epochs
    initial_start = parse_input_to_epoch(start_str, PAIR)
    initial_end   = parse_input_to_epoch(end_str, PAIR)

    print(f"[App] Time Config ({PAIR}):")
    print(f"      Input TZ:    {INPUT_TZS[PAIR]}")
    print(f"      Input Start: {start_str} -> Epoch: {initial_start}")
    print(f"      Input End:   {end_str}   -> Epoch: {initial_end}")

    # 3. Data Source
    # The CSVDataSource internally knows NQ is 'America/Chicago' (Exchange Time).
    # It will read the CSV (Chicago), convert to UTC, and filter using the UTC epochs we calculated above.
    ds = CSVDataSource(
        pair=PAIR,
        filename=csv_file,
        initial_start_time=initial_start,
        initial_end_time=initial_end,
        bars_per_second=bps,
    )

    # 4. Strategy Logic
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