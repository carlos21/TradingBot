# src/app_factory.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List
import os
import sys
import time

from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

from src.bars_loader import BarsLoader
from src.controllers.lines_controller import LinesController
from src.controllers.trades_controller import TradesController
from src.controllers.admin_controller import AdminController
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.events.event_bus import EventBus
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.event_publisher import (
    CompositeEventPublisher,
    DomainEventBusPublisher,
    SocketIOEventPublisher,
)
from src.services.trade_manager import TradeManager
from src.services.trade_executor import TradeExecutor
from src.infrastructure.gateway.executor import MultiAccountExecutor
from src.services.trade_logger import TradeLogger
from src.services.analytics_service import AnalyticsService
from src.financial_calc import FinancialCalc
from src.strategies.base_liquidity_strategy import StrategyOptions, LineRemovalMode
from src.domain.repositories import (
    LineRepository,
    TradeRepository,
    LineTriggerStateRepository,
)
from src.infrastructure.repositories.line_trigger_state_repository import InMemoryLineTriggerStateRepository
from src.infrastructure.repositories.decision_log_repository import DecisionLogRepository
from src.infrastructure.repositories.settings_repository import SettingsRepository
from src.infrastructure.repositories.accounts_repository import NtAccountRepository
from src.infrastructure.repositories.credentials_repository import CredentialRepository
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
    register_settings_routes,
    register_mt_routes,
    register_nt_routes,
    register_stream_routes,
)


@dataclass
class Repositories:
    lines: LineRepository
    trades: TradeRepository
    trigger_state: LineTriggerStateRepository = None
    decision_logs: DecisionLogRepository = None

    def __post_init__(self):
        if self.trigger_state is None:
            self.trigger_state = InMemoryLineTriggerStateRepository()
        if self.decision_logs is None:
            self.decision_logs = DecisionLogRepository()


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
            if strategy.options.breakeven or strategy.options.reentry_breakeven:
                strategy._check_breakeven(bar)
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
            # Strategy runs first (entry triggers, phantom exits, line management)
            strategy.on_raw_bar(bar)
            # TradeManager checks real trades for SL/TP and emits TRADE_CLOSED events
            trade_manager.handle_new_1m_bar(bar)
            # Breakeven is applied only after SL/TP is resolved for the bar
            if strategy.options.breakeven or strategy.options.reentry_breakeven:
                strategy._check_breakeven(bar)
            # Reentries are handled inside strategy.on_raw_bar() on subsequent bars
            # after the SL hit. Same-bar reentries are blocked by sl_bar_time guard.
        
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
            strategy.is_warmup = True
            strategy._warmup_crossed_lines.clear()
            start = __import__('time').monotonic()
            for i, bar in enumerate(bars):
                strategy.on_raw_bar(bar)

            strategy.is_warmup = False
            strategy.restore_trigger_states(pair)
            strategy.restore_open_trades()
            strategy.restore_reentry_opportunities(pair)

            # Remove lines that were touched during warmup. A touched line is a stale
            # setup — the bounce already happened and the opportunity has passed.
            stale_count = 0
            for sid in list(strategy._warmup_crossed_lines):
                line = strategy.strategy_lines.get(sid)
                if line is None:
                    continue
                # Only remove if the line was actually touched (interaction_ts set)
                if line.get('interaction_ts') is None:
                    continue
                if strategy.options.line_removal_mode != LineRemovalMode.NEVER:
                    strategy.remove_strategy_line(sid)
                    stale_count += 1
                strategy._warmup_crossed_lines.discard(sid)
            if stale_count:
                logger.info(f"[LiveMode] Removed {stale_count} stale line(s) touched during warmup")
            
            elapsed = __import__('time').monotonic() - start
            logger.info(f"[LiveMode] Warmup complete in {elapsed:.1f}s, ready for live bars.")
            
            # Tell any connected browsers to reload chart data
            try:
                socketio.emit('history_ready', {'count': len(bars)})
            except Exception as e:
                logger.error(f"[LiveMode] Failed to emit history_ready: {type(e).__name__}: {e}")
        except Exception as e:
            strategy.is_warmup = False
            logger.error(f"[LiveMode] ERROR during warmup: {type(e).__name__}: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    import threading
    _warmup_running = threading.Event()
    
    def _on_history_complete(bars):
        """Return immediately, process bars in background thread."""
        if _warmup_running.is_set():
            logger.warning(f"[LiveMode] Warmup already in progress, ignoring duplicate history_end ({len(bars)} bars)")
            return
        _warmup_running.set()
        logger.info(f"[LiveMode] Received {len(bars)} historical bars, starting background warmup...")
        # Start background thread to process bars - don't block HTTP response
        def _do_warmup_guarded(bars):
            try:
                _do_warmup(bars)
            finally:
                _warmup_running.clear()
        thread = threading.Thread(target=_do_warmup_guarded, args=(bars,), name="HistoryWarmup")
        thread.daemon = True
        thread.start()
    
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
    logger: Optional[ILogger] = None,
    db: DatabaseProtocol | None = None,
    accounts_repo=None,
    app_config=None,
    session_end_time: str | None = None,
    session_tz: str = "America/New_York",
) -> AppWiring:
    """
    Build the whole application with injected dependencies.
    No env vars; no global singletons.
    """
    app = Flask(__name__)
    CORS(app)
    # Use threading async mode for better performance with local NinjaTrader
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

    # Make platform_type available to all templates
    _platform_type = getattr(app_config, "platform_type", "ninjatrader") if app_config else "ninjatrader"
    _platform_label = "NinjaTrader" if _platform_type == "ninjatrader" else "MetaTrader"

    @app.context_processor
    def inject_platform():
        return {
            "platform_type": _platform_type,
            "platform_label": _platform_label,
            "is_ninjatrader": _platform_type == "ninjatrader",
            "is_metatrader": _platform_type == "metatrader",
        }

    # Wire domain event bus → SocketIO bridge for decoupled notifications
    event_bus = EventBus()
    from src.events.event_bus import SocketIOBridge
    socketio_bridge = SocketIOBridge(socketio, event_bus)
    socketio_bridge.start()

    # Publisher goes through EventBus; SocketIOBridge forwards to SocketIO
    # so every event reaches SocketIO exactly once.
    event_publisher = DomainEventBusPublisher(event_bus)
    
    _setup_logging(app)
    
    # Create the appropriate logger based on mode
    if logger is None:
        if live_mode:
            logger = FileAndConsoleLogger(log_dir="logs")
        else:
            logger = ConsoleLogger()
    
    if notifier is None:
        notifier = NoOpNotifier()
    if analytics is None:
        analytics = NoOpReporter()
    analytics.set_context("app", {"pair": pair, "live_mode": live_mode})

    trade_logger = TradeLogger(repos.trades)

    if accounts_repo is None and db is not None:
        accounts_repo = NtAccountRepository(db=db)

    _resolved_session_end = session_end_time or (getattr(app_config, "session_end", None) if app_config else None) or "16:58"

    trade_manager = TradeManager(
        trade_repository=repos.trades,
        socketio=event_publisher,
        pair=pair,
        session_end_time=_resolved_session_end,
        session_tz=session_tz,
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
        accounts_repo=accounts_repo,
    )

    # Inject trade_manager into MultiAccountExecutor (created before trade_manager existed)
    if isinstance(trade_executor, MultiAccountExecutor):
        trade_executor.trade_manager = trade_manager
    
    # Initialize strategy BEFORE registering ZMQ callbacks so closures can reference it safely
    tstrategy = LiquidityStrategyV2(
        min_stop_loss   = numbers.min_stop_loss,
        max_bounce      = numbers.max_bounce,
        socketio        = event_publisher,
        line_repository = repos.lines,
        trade_repository= repos.trades,
        trade_manager   = trade_manager,
        extra_sl_space  = numbers.extra_sl_space,
        fixed_stop_loss = numbers.fixed_stop_loss,
        max_stop_loss   = numbers.max_stop_loss,
        sl_levels       = numbers.sl_levels,
        max_entry_distance = numbers.max_entry_distance,
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
        decision_log_repository = repos.decision_logs,
        account_configs = numbers.account_configs,
        accounts_repo=accounts_repo,
    )

    # Wire strategy to TRADE_CLOSED events so it updates state reactively
    # (same code path for live and backtest — broker is source of truth for exits)
    from src.domain.events import EventType
    event_bus.add_subscriber(EventType.TRADE_CLOSED, tstrategy)
    
    # Wire up position sync handler for crash recovery (ZeroMQ only)
    # Broker (NinjaTrader) is the source of truth - it reports actual positions to Python
    if isinstance(data_source, ZMQDataSource) and data_source.gateway is not None:
        def _handle_position_sync(payload):
            """Reconcile Python state with broker reality after reconnect.
            
            Broker (NinjaTrader) is the source of truth. If there's a mismatch,
            we update Python's state to match the broker.
            """
            from datetime import datetime, timezone
            positions = payload.get('positions', [])
            broker_trade_ids = {p['trade_id'] for p in positions}
            
            # ------------------------------------------------------------------
            # 1. Broker has positions Python doesn't know about → CREATE them
            # ------------------------------------------------------------------
            python_trade_ids = {t['trade_id'] for t in trade_manager.open_trades}
            for pos in positions:
                trade_id = pos['trade_id']
                if trade_id in python_trade_ids:
                    continue

                # Check DB — trade might exist but wasn't loaded (shouldn't happen)
                db_trade = repos.trades.get_trade(trade_id)
                if db_trade and db_trade.exit_time is None:
                    logger.error(f"[PositionSync] CRITICAL: Trade {trade_id} is open in DB but missing from open_trades — adding back")
                    trade_manager.open_trades.append({
                        'trade_id': db_trade.trade_id,
                        'pair': db_trade.pair,
                        'type': db_trade.trade_type,
                        'entry': db_trade.entry_price,
                        'stop_loss': db_trade.stop_loss,
                        'take_profit': db_trade.take_profit,
                        'risk': db_trade.risk,
                        'risk_dollars': db_trade.risk_dollars,
                        'risk_pct': db_trade.risk_pct,
                        'contracts': db_trade.contracts,
                        'status': 'open',
                        'entry_time': db_trade.entry_time.timestamp(),
                    })
                    continue

                if db_trade and db_trade.exit_time is not None:
                    logger.error(f"[PositionSync] DISCREPANCY: Broker has open position {trade_id} but DB shows it closed at {db_trade.exit_time}. Re-opening from broker data.")

                # Create trade from broker data so session-end close can manage it
                direction = pos.get('direction', 'long')
                entry_price = float(pos.get('entry_price', 0))
                stop_loss = pos.get('stop_loss')
                take_profit = pos.get('take_profit')
                quantity = float(pos.get('quantity', 1))
                account_name = pos.get('account')

                # Use broker SL/TP if available, otherwise sensible defaults
                if stop_loss is None:
                    stop_loss = entry_price - 20.0 if direction == 'long' else entry_price + 20.0
                if take_profit is None:
                    take_profit = entry_price + 100.0 if direction == 'long' else entry_price - 100.0

                risk = abs(entry_price - stop_loss)
                entry_time = time.time()
                rr_ratio = numbers.rr_ratio if 'numbers' in dir() else 5.0

                try:
                    trade = trade_manager.open_trade(
                        pair=pair,
                        trade_type=direction,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        risk=risk,
                        entry_time=entry_time,
                        rr_ratio=rr_ratio,
                        source="broker_sync",
                        account=account_name,
                        trade_id=trade_id,  # Preserve broker trade_id so future fills match
                    )
                    # Override contracts to match broker quantity
                    for ot in trade_manager.open_trades:
                        if ot['trade_id'] == trade['trade_id']:
                            ot['contracts'] = quantity
                    logger.info(f"[PositionSync] Created trade {trade['trade_id']} from broker position {trade_id} ({direction} @ {entry_price}, qty={quantity})")
                except Exception as e:
                    logger.error(f"[PositionSync] Failed to create trade from broker position {trade_id}: {e}")

            # ------------------------------------------------------------------
            # 2. Python has trades broker doesn't report → log, but DON'T auto-close
            #    on the first sync.  Broker tracking may still be restoring.
            # ------------------------------------------------------------------
            python_trade_ids = {t['trade_id'] for t in trade_manager.open_trades}
            for trade in list(trade_manager.open_trades):
                if trade['trade_id'] not in broker_trade_ids:
                    logger.warning(f"[PositionSync] Trade {trade['trade_id']} not reported by broker. Keeping open in Python — will retry at session end.")
            
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
                # For multi-account: only notify strategy when ALL account trades closed
                if isinstance(trade_executor, MultiAccountExecutor):
                    signal_id = trade_executor.get_signal_id(trade_id)
                    if signal_id and trade_executor.all_account_trades_closed(signal_id):
                        tstrategy.handle_broker_exit_fill(signal_id, exit_price, result_type)
                        logger.info(f"[BrokerFill] Exit fill handled for signal {signal_id} (all accounts closed)")
                    else:
                        logger.info(f"[BrokerFill] Exit fill handled for account trade {trade_id} @ {exit_price} ({result_type})")
                else:
                    tstrategy.handle_broker_exit_fill(trade_id, exit_price, result_type)
                    logger.info(f"[BrokerFill] Exit fill handled for {trade_id} @ {exit_price} ({result_type})")

        data_source.gateway.on_entry_fill(_handle_entry_fill)
        data_source.gateway.on_exit_fill(_handle_exit_fill)
        logger.info("[ZMQ] Broker fill handlers registered")

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
    trades_controller = TradesController(loader, trade_manager, logger=logger, rr_ratio=numbers.rr_ratio, strategy=tstrategy)
    
    # Initialize analytics service and admin controller
    analytics_service = AnalyticsService(repos.trades)
    admin_controller = AdminController(analytics_service, repos.lines, logger=logger, decision_log_repository=repos.decision_logs)

    # Initialize settings/manager/NT services from DB
    from src.services.settings_service import SettingsService
    from src.infrastructure.repositories.settings_repository import SettingsRepository
    from src.infrastructure.repositories.accounts_repository import NtAccountRepository
    from src.infrastructure.repositories.credentials_repository import CredentialRepository
    from src.services.nt_manager_service import NtManagerService
    from src.services.platform_deploy_service import PlatformDeployService
    from src.controllers.settings_controller import SettingsController

    secret_key = os.environ.get("SECRET_KEY")
    settings_repo = SettingsRepository(db=db)
    accounts_repo = NtAccountRepository(db=db)
    creds_repo = CredentialRepository(db=db)
    settings_service = SettingsService(settings_repo, accounts_repo, creds_repo, secret_key=secret_key)
    settings_controller = SettingsController(settings_service)
    nt_service = NtManagerService()
    deploy_service = PlatformDeployService()

    # Build platform-specific lifecycle service (SOLID: one implementation per platform)
    if _platform_type == "ninjatrader":
        from src.services.platform_lifecycle.nt_lifecycle_service import NinjaTraderLifecycleService
        platform_lifecycle = NinjaTraderLifecycleService(nt_service, settings_service, logger)
    else:
        from src.services.platform_lifecycle.mt_lifecycle_service import MetaTraderLifecycleService
        from src.services.mt_manager_service import MetaTraderManagerService
        mt_service = MetaTraderManagerService()
        platform_lifecycle = MetaTraderLifecycleService(mt_service, logger, settings_service=settings_service)

    # Optionally load any preexisting lines from repo into the in-memory strategy
    if bootstrap_existing_lines:
        for l in repos.lines.list_lines(pair):
            # Use the line's actual creation date so historical bars from BEFORE
            # the line was drawn are correctly skipped during warmup.
            tstrategy.add_strategy_line(l.line_id, l.price, creation_timestamp=l.creation_date.timestamp())
        if live_mode:
            tstrategy.restore_open_trades()

    # Register routes
    register_core_routes(app, pair, data_source, logger=logger)
    register_lines_routes(app, lines_controller, logger)
    register_trades_routes(app, trades_controller, repos.trades, pair, trade_logger, logger)
    register_admin_routes(app, admin_controller, logger)
    register_settings_routes(app, settings_controller, logger)
    register_nt_routes(app, nt_service, deploy_service, logger)
    if _platform_type == "metatrader":
        register_mt_routes(app, mt_service, deploy_service, logger)
    register_stream_routes(app, data_source, platform_lifecycle, socketio, logger)
    register_debug_routes(
        app, tstrategy, loader, trade_manager, repos.lines, repos.trades,
        data_source, pair, notifier, analytics, logger=logger
    )
    register_socketio_handlers(socketio, loader, data_source, live_mode, logger)
    
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
