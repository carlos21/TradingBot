import io
from src.strategies.liquidity_strategy import LiquidityStrategy, StrategyOptions, LineRemovalMode
from src.strategies.entry_context import retest_cross_trigger, open_trades_limit_filter, max_bounce_filter
from src.strategies.strategy_config import StrategyConfig
from src.bars_loader import BarsLoader
from src.services.trade_manager import TradeManager
from src.data_sources.csv_datasource import CSVDataSource
from tests.fakes import DummySocketIO, FakeLineRepository, FakeTradeRepository


def build_system(csv_text: str,
                 pair='NQ',
                 strategy_tf='1m',
                 min_sl=10.0,
                 max_bounce=40.0,
                 extra_sl=0.0,
                 line_removal=LineRemovalMode.NEVER,
                 filters=None,
                 triggers=None):
    sock   = DummySocketIO()
    lines  = FakeLineRepository()
    trades = FakeTradeRepository()

    options = StrategyOptions(
        line_removal_mode=line_removal,
        triggers=triggers or [retest_cross_trigger],
        entry_filters=filters or [open_trades_limit_filter(1), max_bounce_filter(max_bounce)]
    )

    strat = LiquidityStrategy(
        min_stop_loss=min_sl,
        max_bounce=max_bounce,
        extra_sl_space=extra_sl,
        socketio=sock,
        line_repository=lines,
        trade_repository=trades,
        options=options,
        strategy_tf=strategy_tf
    )

    tm = TradeManager(trade_repository=trades, socketio=sock)

    ds = CSVDataSource(
        pair=pair,
        time_fmt='%d/%m/%Y %H:%M:%S',
        tz='America/Chicago', # NQ
        bars_per_second=10.0,
        fileobj=io.StringIO(csv_text)
    )

    def combined_bar_callback(bar):
        tm.handle_new_1m_bar(bar) # closes by 1m extremes
        strat.on_raw_bar(bar) # opens by strategy TF, closes by TF extremes

    loader = BarsLoader(
        data_source=ds,
        socketio=sock,
        bar_callback=combined_bar_callback
    )
    return strat, tm, lines, trades, loader, sock


def seed_lines(strat, lines):
    """
    lines = [
        {"id": "L1", "level": 19560.0, "direction": "short"},
        {"id": "L2", "level": 19540.0, "direction": "short"},
    ]
    """
    for L in lines:
        strat.add_strategy_line(L["id"], L["level"], L["direction"])