# src/app_factory.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List
import time

from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

from src.bars_loader import BarsLoader
from src.controllers.lines_controller import LinesController
from src.controllers.trades_controller import TradesController
from src.controllers.admin_controller import AdminController
from src.data_sources.combined_datasource import CombinedDataSource
from src.gateway.datasource import ZMQDataSource
from src.services.trade_manager import TradeManager
from src.services.trade_executor import TradeExecutor
from src.services.trade_logger import TradeLogger
from src.services.analytics_service import AnalyticsService
from src.financial_calc import FinancialCalc
from src.strategies.base_liquidity_strategy import StrategyOptions
from src.repositories.lines_repository import LineRepository
from src.repositories.trades_repository import TradeRepository
from src.repositories.line_trigger_state_repository import LineTriggerStateRepository, InMemoryLineTriggerStateRepository
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2
from src.strategies.strategy_config import CandleConfig, StrategyNumbers
from src.notifier import Notifier, NoOpNotifier
from src.analytics import AnalyticsReporter, NoOpReporter
from src.utils.app_logger import ILogger, ConsoleLogger, FileAndConsoleLogger

# Route modules
from src.routes import (
    register_core_routes,
    register_lines_routes,
    register_trades_routes,
    register_admin_routes,
    register_debug_routes,
    register_socketio_handlers,
)


@dataclass
class Repositories:
    lines: LineRepository
    trades: TradeRepository
    trigger_state: LineTriggerStateRepository = None

    def __post_init__(self):
        if self.trigger_state is None:
            self.trigger_state = InMemoryLineTriggerStateRepository()


@dataclass
class AppWiring:
    app: Flask
    socketio: SocketIO
    loader: BarsLoader
    strategy: LiquidityStrategyV2
    trade_manager: TradeManager
    lines_controller: LinesController
    trades_controller: TradesController
    admin_controller: AdminController
    data_source: CombinedDataSource
    pair: str
    live_mode: bool = False
    logger: Optional[ILogger] = None


def _setup_logging(app: Flask):
    """Configure logging filters for high-frequency endpoints."""
    import logging
    class _QuietFilter(logging.Filter):
        _NOISY = ('/api/nt/tick', '/api/nt/partial')
        def filter(self, record):
            msg = record.getMessage()
            return not any(p in msg for p in self._NOISY)
    logging.getLogger('werkzeug').addFilter(_QuietFilter())


def _create_bar_callbacks(
    live_mode: bool,
    trade_manager: TradeManager,
    strategy: LiquidityStrategyV2,
    loader: BarsLoader,
    data_source: CombinedDataSource,
    repos: Repositories,
    pair: str,
    logger: ILogger,
):
    """Create bar processing callbacks based on mode.
    
    Returns (bar_callback, stream_end_callback) tuple.
    """
    if live_mode:
        _close_commands_sent: set = set()
        
        def _check_live_session_end(bar):
            """Send close commands to NinjaTrader when session ends."""
            if not trade_manager._session_end_time or not trade_manager._session_tz:
                return
            from datetime import datetime
            from zoneinfo import ZoneInfo
            bar_dt = datetime.fromtimestamp(bar['time'], tz=trade_manager._session_tz)
            if bar_dt.time() < trade_manager._session_end_time:
                return
            for t in list(trade_manager.open_trades):
                if t['pair'] != bar['pair'] or t['entry_time'] > bar['time']:
                    continue
                tid = t['trade_id']
                if tid not in _close_commands_sent:
                    logger.info(f"[LiveMode] SESSION END — sending close_order to NT for {tid}")
                    trade_manager.trade_logger.log(tid, "SESSION_END", "Sending close_order to NinjaTrader")
                    trade_manager.trade_logger.log(tid, "CMD_SENT", "close_order → NinjaTrader")
                    trade_manager.trade_executor.on_trade_close(tid, bar['close'])
                    _close_commands_sent.add(tid)
        
        def combined_bar_callback(bar):
            # No trade_manager.handle_new_1m_bar — NinjaTrader handles SL/TP
            strategy.on_raw_bar(bar)
            _check_live_session_end(bar)
        
        def stream_end_callback(close_price: float, final_time: float):
            # Send close commands to NT, don't close locally
            for t in list(trade_manager.open_trades):
                tid = t['trade_id']
                if tid not in _close_commands_sent:
                    logger.info(f"[LiveMode] STREAM END — sending close_order to NT for {tid}")
                    trade_manager.trade_logger.log(tid, "SESSION_END", "Stream end — sending close_order to NinjaTrader")
                    trade_manager.trade_logger.log(tid, "CMD_SENT", "close_order → NinjaTrader")
                    trade_manager.trade_executor.on_trade_close(tid, close_price)
                    _close_commands_sent.add(tid)
    else:
        def combined_bar_callback(bar):
            trade_manager.handle_new_1m_bar(bar)
            strategy.on_raw_bar(bar)
        
        def stream_end_callback(close_price: float, final_time: float):
            trade_manager.close_remaining_trades_at_stream_end(close_price, final_time)
    
    return combined_bar_callback, stream_end_callback


def _setup_live_mode_callbacks(
    data_source: CombinedDataSource,
    strategy: LiquidityStrategyV2,
    loader: BarsLoader,
    repos: Repositories,
    pair: str,
    logger: ILogger,
    socketio: SocketIO,
):
    """Setup callbacks for live mode data source."""
    def _do_warmup(bars):
        """Background task: process historical bars."""
        try:
            start = __import__('time').monotonic()
            for i, bar in enumerate(bars):
                strategy.on_raw_bar(bar)
                # Log progress every 5000 bars
                if (i + 1) % 5000 == 0:
                    logger.info(f"[LiveMode] Warmup progress: {i+1}/{len(bars)} bars...")
            
            strategy.restore_trigger_states(pair)
            strategy.restore_open_trades()
            strategy.restore_reentry_opportunities(pair)
            
            elapsed = __import__('time').monotonic() - start
            logger.info(f"[LiveMode] Warmup complete in {elapsed:.1f}s, ready for live bars.")
            
            # Tell any connected browsers to reload chart data
            try:
                socketio.emit('history_ready', {'count': len(bars)})
            except Exception as e:
                logger.error(f"[LiveMode] Failed to emit history_ready: {type(e).__name__}: {e}")
        except Exception as e:
            logger.error(f"[LiveMode] ERROR during warmup: {type(e).__name__}: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _on_history_complete(bars):
        """Return immediately, process bars in background task."""
        logger.info(f"[LiveMode] Received {len(bars)} historical bars, starting background warmup...")
        # Start background task to process bars - don't block HTTP response
        socketio.start_background_task(_do_warmup, bars)
    
    def _on_live_bar(bar):
        # Route through BarsLoader so bars get aggregated into
        # the current timeframe (5m, 15m, etc.) before chart emission.
        loader._handle_message(bar)
    
    def _on_before_refresh():
        """Reset strategy and re-add DB lines before fresh bars arrive."""
        logger.info("[LiveMode] Refresh: resetting strategy...")
        strategy.reset(preserve_trigger_state=True)
        loader.reset()
        # Re-add persistent lines with their real creation timestamp so the
        # existing guards in liquidity_strategy_v2 skip historical bars that
        # predate when the line was drawn.  creation_date is always UTC-aware
        # (the repo enforces this), so .timestamp() gives correct epoch seconds.
        for l in repos.lines.list_lines(pair):
            strategy.add_strategy_line(l.line_id, l.price, creation_timestamp=l.creation_date.timestamp())
        logger.info("[LiveMode] Refresh: strategy reset, ready for fresh bars.")
    
    if isinstance(data_source, ZMQDataSource):
        data_source.on_history_complete = _on_history_complete
        data_source.on_live_bar = _on_live_bar
        data_source.on_before_refresh = _on_before_refresh


def create_app(
    *,
    pair: str,
    data_source: CombinedDataSource,
    repos: Repositories,
    numbers: StrategyNumbers,
    options: Optional[StrategyOptions] = None,
    candle_config: Optional[CandleConfig] = None,
    timeframes: Optional[List[str]] = None,
    bootstrap_existing_lines: bool = True,
    broker_mode: str = 'futures',
    broker_spread: float = 0.0,
    use_fractional_lots: bool = False,
    fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
    live_mode: bool = False,
    trade_executor: TradeExecutor = None,
    notifier: Notifier = None,
    analytics: AnalyticsReporter = None,
) -> AppWiring:
    """
    Build the whole application with injected dependencies.
    No env vars; no global singletons.
    """
    app = Flask(__name__)
    CORS(app)
    # Use eventlet for production-grade async server (replaces Werkzeug dev server)
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
    
    _setup_logging(app)
    
    # Create the appropriate logger based on mode
    if live_mode:
        logger: ILogger = FileAndConsoleLogger(log_dir="logs")
    else:
        logger: ILogger = ConsoleLogger()
    
    if notifier is None:
        notifier = NoOpNotifier()
    if analytics is None:
        analytics = NoOpReporter()
    analytics.set_context("app", {"pair": pair, "live_mode": live_mode})

    trade_logger = TradeLogger(repos.trades)

    trade_manager = TradeManager(
        trade_repository=repos.trades,
        socketio=socketio,
        pair=pair,
        session_end_time="17:00",
        session_tz="America/New_York",
        broker_mode=broker_mode,
        broker_spread=broker_spread,
        use_fractional_lots=use_fractional_lots,
        fee_per_rt=fee_per_rt,
        trade_executor=trade_executor,
        trade_logger=trade_logger,
        notifier=notifier,
        analytics=analytics,
        point_value=numbers.point_value,
        account_balance=numbers.account_balance,
        risk_per_trade=numbers.risk_per_trade,
        risk_pct_per_trade=numbers.risk_pct_per_trade,
        logger=logger,
    )
    
    # Wire up position sync handler for crash recovery (ZeroMQ only)
    # Broker (NinjaTrader) is the source of truth - it reports actual positions to Python
    if isinstance(data_source, ZMQDataSource) and data_source.gateway is not None:
        def _handle_position_sync(payload):
            """Reconcile Python state with broker reality after reconnect.
            
            Broker (NinjaTrader) is the source of truth. If there's a mismatch,
            we update Python's state to match the broker.
            """
            positions = payload.get('positions', [])
            broker_trade_ids = {p['trade_id'] for p in positions}
            
            # Find trades Python thinks are open but broker doesn't have
            for trade in list(trade_manager.open_trades):
                if trade['trade_id'] not in broker_trade_ids:
                    logger.warning(f"[PositionSync] Trade {trade['trade_id']} not found on broker - closing in Python")
                    trade_manager.close_trade(
                        trade_id=trade['trade_id'],
                        exit_price=trade['entry'],  # Assume flat
                        exit_time=time.time(),
                    )
            
            # Log any broker positions Python doesn't know about
            python_trade_ids = {t['trade_id'] for t in trade_manager.open_trades}
            for pos in positions:
                if pos['trade_id'] not in python_trade_ids:
                    logger.warning(f"[PositionSync] Broker has position {pos['trade_id']} that Python doesn't know about")
            
            logger.info(f"[PositionSync] Reconciliation complete: {len(positions)} broker position(s), {len(trade_manager.open_trades)} Python position(s)")
        
        data_source.gateway.on_position_sync(_handle_position_sync)
        logger.info("[ZMQ] Position sync handler registered for crash recovery")

        # Wire up broker fill handlers so Python chart reflects actual NinjaTrader state
        def _handle_entry_fill(payload):
            trade_id = payload.get('trade_id')
            entry_price = payload.get('entry_price')
            stop_loss = payload.get('stop_loss')
            take_profit = payload.get('take_profit')
            if trade_id and entry_price is not None:
                trade_manager.handle_broker_entry_fill(trade_id, entry_price, stop_loss, take_profit)
                logger.info(f"[BrokerFill] Entry fill handled for {trade_id} @ {entry_price}")

        def _handle_exit_fill(payload):
            trade_id = payload.get('trade_id')
            exit_price = payload.get('exit_price')
            result_type = payload.get('result_type', 'CLOSE')
            if trade_id and exit_price is not None:
                trade_manager.handle_broker_fill(trade_id, exit_price, result_type)
                tstrategy.handle_broker_exit_fill(trade_id, exit_price, result_type)
                logger.info(f"[BrokerFill] Exit fill handled for {trade_id} @ {exit_price} ({result_type})")

        data_source.gateway.on_entry_fill(_handle_entry_fill)
        data_source.gateway.on_exit_fill(_handle_exit_fill)
        logger.info("[ZMQ] Broker fill handlers registered")

    # Initialize strategy
    tstrategy = LiquidityStrategyV2(
        min_stop_loss   = numbers.min_stop_loss,
        max_bounce      = numbers.max_bounce,
        socketio        = socketio,
        line_repository = repos.lines,
        trade_repository= repos.trades,
        trade_manager   = trade_manager,
        extra_sl_space  = numbers.extra_sl_space,
        fixed_stop_loss = numbers.fixed_stop_loss,
        max_stop_loss   = numbers.max_stop_loss,
        sl_levels       = numbers.sl_levels,
        sl_level_tolerance = numbers.sl_level_tolerance,
        min_cross_depth = numbers.min_cross_depth,
        rr_ratio        = numbers.rr_ratio,
        point_value     = numbers.point_value,
        account_balance = numbers.account_balance,
        risk_per_trade  = numbers.risk_per_trade,
        risk_pct_per_trade = numbers.risk_pct_per_trade,
        use_fractional_lots = use_fractional_lots,
        fee_per_rt = fee_per_rt,
        broker_spread = broker_spread,
        options         = options,
        timeframes      = timeframes,
        candle_config   = candle_config,
        trade_logger    = trade_logger,
        analytics       = analytics,
        trigger_state_repo = repos.trigger_state,
        logger          = logger,
    )

    # Create bar callbacks based on mode
    bar_callback, stream_end_callback = _create_bar_callbacks(
        live_mode, trade_manager, tstrategy, None, data_source, repos, pair, logger
    )

    loader = BarsLoader(
        data_source=data_source,
        socketio=socketio,
        bar_callback=bar_callback,
        stream_end_callback=stream_end_callback,
        logger=logger,
    )
    loader.live_mode = live_mode
    
    # Update callback to reference loader (for _check_live_session_end)
    bar_callback, stream_end_callback = _create_bar_callbacks(
        live_mode, trade_manager, tstrategy, loader, data_source, repos, pair, logger
    )
    loader.bar_callback = bar_callback
    loader.stream_end_callback = stream_end_callback

    # In live mode, wire direct callbacks on the data source
    if live_mode:
        _setup_live_mode_callbacks(data_source, tstrategy, loader, repos, pair, logger, socketio)

    # Create controllers
    lines_controller = LinesController(repos.lines, loader, tstrategy, logger=logger)
    trades_controller = TradesController(loader, trade_manager, logger=logger, rr_ratio=numbers.rr_ratio)
    
    # Initialize analytics service and admin controller
    analytics_service = AnalyticsService(repos.trades)
    admin_controller = AdminController(analytics_service, repos.lines, logger=logger)

    # Optionally load any preexisting lines from repo into the in-memory strategy
    if bootstrap_existing_lines:
        for l in repos.lines.list_lines(pair):
            # FIX: Force timestamp to 0 for existing DB lines so they are valid for ALL history.
            # This prevents "future" creation dates (e.g. 2025) from blocking trades on 2024 data.
            tstrategy.add_strategy_line(l.line_id, l.price, creation_timestamp=0)
        if live_mode:
            tstrategy.restore_open_trades()

    # Register routes
    register_core_routes(app, pair, data_source, logger=logger)
    register_lines_routes(app, lines_controller, logger=logger)
    register_trades_routes(app, trades_controller, repos.trades, pair, trade_logger, logger=logger)
    register_admin_routes(app, admin_controller, logger=logger)
    register_debug_routes(
        app, tstrategy, loader, trade_manager, repos.lines, repos.trades,
        data_source, pair, notifier, analytics, logger=logger
    )
    register_socketio_handlers(socketio, loader, data_source, live_mode, logger=logger)
    
    from werkzeug.exceptions import HTTPException

    # Global error handlers for API routes
    @app.errorhandler(400)
    def handle_400(error):
        return jsonify({"error": str(error.description)}), 400

    @app.errorhandler(404)
    def handle_404(error):
        return jsonify({"error": str(error.description)}), 404

    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        return jsonify({"error": str(error.description)}), error.code

    @app.errorhandler(500)
    def handle_500(error):
        notifier.send(f"[Flask] Unhandled server error: {error}")
        return jsonify({"error": "Internal server error"}), 500

    return AppWiring(
        app=app,
        socketio=socketio,
        loader=loader,
        strategy=tstrategy,
        trade_manager=trade_manager,
        lines_controller=lines_controller,
        trades_controller=trades_controller,
        admin_controller=admin_controller,
        data_source=data_source,
        pair=pair,
        live_mode=live_mode,
        logger=logger,
    )
