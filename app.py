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

# --- 1. INPUT TIMEZONE ---
# This controls how the DATES you type below (start_str, end_str) are interpreted.
# We set this to 'America/New_York' so you can type "09:00" and mean 9 AM EST/EDT.
INPUT_TZ = 'America/New_York'

# --- 2. FILE TIMEZONE ---
# Your CSV has 10:00 for an event that is 09:00 NY (13:00 UTC).
# This means your CSV is UTC-3 (10:00 + 3h = 13:00).
# 'Etc/GMT+3' is the standard ZoneInfo code for UTC-3 (Fixed Offset).
FILE_TZ = 'Etc/GMT+3'

def parse_input_to_epoch(date_str: str, tz_name: str) -> int:
    """
    Parses a date string (ISO-like) using the specified timezone,
    then converts it to a UTC epoch timestamp.
    """
    if not date_str:
        return None
        
    input_tz = ZoneInfo(tz_name)
    
    # Parse string to datetime (naive)
    try:
        dt = datetime.fromisoformat(date_str)
    except ValueError:
        # Fallback for simple space-separated format
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

    # Attach the timezone you expect
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

    # --- HARDCODED DATES (Based on INPUT_TZ) ---
    # You can now enter dates exactly as you see them on the chart (NY Time).
    start_str = "2024-03-01 08:00:00"
    end_str   = "2024-04-03 20:00:00"

    # Convert Input -> UTC Epochs
    initial_start = parse_input_to_epoch(start_str, INPUT_TZ)
    initial_end   = parse_input_to_epoch(end_str, INPUT_TZ)

    print(f"[App] Time Config ({PAIR}):")
    print(f"      Input TZ (User): {INPUT_TZ}")
    print(f"      File  TZ (CSV):  {FILE_TZ}")
    print(f"      Input Start:     {start_str} -> Epoch: {initial_start}")
    print(f"      Input End:       {end_str}   -> Epoch: {initial_end}")

    # 3. Data Source
    # We explicitly pass tz=FILE_TZ so the loader knows how to read the CSV.
    ds = CSVDataSource(
        pair=PAIR,
        filename=csv_file,
        initial_start_time=initial_start,
        initial_end_time=initial_end,
        bars_per_second=bps,
        tz=FILE_TZ 
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