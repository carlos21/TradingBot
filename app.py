from datetime import datetime
from app_factory import create_app, Repositories, StrategyNumbers
from src.repositories.lines_repository import SQLLineRepository
from src.repositories.trades_repository import SQLTradeRepository
from src.data_sources.csv_datasource import CSVDataSource
from src.strategies.liquidity_strategy import StrategyOptions, LineRemovalMode
from src.strategies.entry_context import retest_cross_trigger, open_trades_limit_filter, max_bounce_filter
from src.database import database

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
        initial_start_time=datetime.fromisoformat("2024-01-14T00:00:00+00:00"),
        initial_end_time  =datetime.fromisoformat("2024-05-15T00:00:00+00:00"),
        bars_per_second=10.0,
    )

    numbers = StrategyNumbers(
        min_stop_loss=10.0,
        max_bounce=60.0,
        extra_sl_space=0.0,
    )

    options = StrategyOptions(
        line_removal_mode=LineRemovalMode.ON_EVALUATE,
        # Triggers are now handled by LiquidityStrategyV2 defaults (wick + 3-candle)
        # unless you override them here.
        triggers=None, 
        entry_filters=[
            open_trades_limit_filter(1), 
            max_bounce_filter(numbers.max_bounce)
        ]
    )

    return create_app(
        pair=PAIR,
        data_source=ds,
        repos=repos,
        numbers=numbers,
        options=options,
        timeframes=["5m", "15m"],
        bootstrap_existing_lines=True,
    )

if __name__ == "__main__":
    wiring = build_prod()
    wiring.socketio.run(wiring.app, debug=True, port=5001)