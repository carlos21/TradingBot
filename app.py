from datetime import datetime
from app_factory import create_app, Repositories, StrategyNumbers
from src.repositories.lines_repository import SQLLineRepository
from src.repositories.trades_repository import SQLTradeRepository
from src.data_sources.csv_datasource import CSVDataSource
from src.strategies.base_liquidity_strategy import BreakevenConfig
from src.strategies.liquidity_strategy import StrategyOptions, LineRemovalMode
from src.strategies.entry_context import retest_cross_trigger, open_trades_limit_filter, max_bounce_filter
from src.database import database
from src.strategies.strategy_config import CandleConfig
from src.strategies.triggers import three_candle_reversal_trigger, wick_near_line_trigger

import os
 

PAIR = "NQ"

def build_prod():
    # DB
    database.setup_database()
    repos = Repositories(
        lines = SQLLineRepository(),
        trades= SQLTradeRepository(),
    )

    # Data Source (pick your real source)
    ds = CSVDataSource(
        pair=PAIR,
        initial_start_time=datetime.fromisoformat("2024-04-01T00:00:00+00:00"),
        initial_end_time  =datetime.fromisoformat("2024-05-01T08:40:00+00:00"),
        bars_per_second=10.0,
    )

    numbers = StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=60.0,
        extra_sl_space=0.0,
    )

    removal_mode_env = os.environ.get("LINE_REMOVAL_MODE", "ON_EVALUATE").upper()
    removal_mode = LineRemovalMode.NEVER if removal_mode_env == "NEVER" else LineRemovalMode.ON_EVALUATE
    options = StrategyOptions(
        line_removal_mode=removal_mode,
        entry_filters=[
            open_trades_limit_filter(1), 
            max_bounce_filter(numbers.max_bounce)
        ],
        triggers=[
            wick_near_line_trigger,
            three_candle_reversal_trigger
        ],
        breakeven=BreakevenConfig(
            trigger_rr=2.0, 
            move_to_rr=0.05 
        )
    )

    candle_config = CandleConfig()

    return create_app(
        pair=PAIR,
        data_source=ds,
        repos=repos,
        numbers=numbers,
        options=options,
        candle_config=candle_config,
        timeframes=["5m", "15m"],
        bootstrap_existing_lines=True,
    )

if __name__ == "__main__":
    wiring = build_prod()
    wiring.socketio.run(wiring.app, debug=True, port=5001)