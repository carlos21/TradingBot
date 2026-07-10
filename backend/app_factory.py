# backend/app_factory.py
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

from src.analytics import AnalyticsReporter, NoOpReporter
from src.application.live_readiness import (
    AlwaysEnabledTradingContext,
    LiveBarBuffer,
    MinimumBarsWarmupPolicy,
    ReadinessMonitor,
    ReadinessTradingContext,
    WarmupOrchestrator,
)
from src.application.parity_service import ParityCheckService
from src.bars_loader import BarsLoader
from src.controllers.admin_controller import AdminController
from src.controllers.trades_controller import TradesController
from src.domain.readiness import ReadinessStateMachine
from src.domain.repositories import (
    LineRepository,
    LineTriggerStateRepository,
    TradeRepository,
)
from src.events.event_bus import EventBus
from src.financial_calc import FinancialCalc
from src.infrastructure.bar_auditor import NinjaTraderBarAuditor
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.event_publisher import (
    DomainEventBusPublisher,
)
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.gateway.executor import MultiAccountExecutor, ZMQTradeExecutor
from src.infrastructure.readiness_progress_adapter import (
    SocketIOReadinessProgressAdapter,
)
from src.infrastructure.market_closure_filter import MarketClosureFilter
from src.infrastructure.parity_checker import NinjaTraderParityChecker
from src.infrastructure.repositories.accounts_repository import NtAccountRepository
from src.infrastructure.repositories.credentials_repository import CredentialRepository
from src.infrastructure.repositories.decision_log_repository import (
    DecisionLogRepository,
)
from src.infrastructure.repositories.line_trigger_state_repository import (
    InMemoryLineTriggerStateRepository,
)
from src.infrastructure.repositories.settings_repository import SettingsRepository
from src.notifier import NoOpNotifier, Notifier

# Route modules
from src.routes import (
    register_admin_routes,
    register_core_routes,
    register_debug_routes,
    register_lines_routes,
    register_mt_routes,
    register_nt_routes,
    register_settings_routes,
    register_socketio_handlers,
    register_stream_routes,
    register_trades_routes,
)
from src.services.analytics_service import AnalyticsService
from src.services.trade_executor import TradeExecutor
from src.services.trade_logger import TradeLogger
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_v2.base_strategy import StrategyOptions
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.controllers.lines_controller import LinesController
from src.strategies.protocols import LiquidityStrategy
from src.strategies.strategy_factory import StrategyFactory
from src.utils.app_logger import (
    ConsoleLogger,
    FileAndConsoleLogger,
    ILogger,
    configure_logging,
)
from src.utils.history_loaded_deduper import HistoryLoadedDeduper


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
    strategy: LiquidityStrategy
    trade_manager: TradeManager
    lines_controller: LinesController
    trades_controller: TradesController
    admin_controller: AdminController
    data_source: CombinedDataSource
    pair: str
    live_mode: bool = False
    logger: Optional[ILogger] = None
    bar_auditor: Any | None = None


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
    strategy: LiquidityStrategy,
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
            bar_dt = datetime.fromtimestamp(bar['time'], tz=trade_manager._session_tz)
            if bar_dt.time() < trade_manager._session_end_time:
                return
            for t in list(trade_manager.open_trades):
                if t['pair'] != bar['pair'] or t['entry_time'] > bar['time']:
                    continue
                if t.get('source') in trade_manager.USER_CONTROLLED_SOURCES:
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
                strategy.check_breakeven(bar)
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
                strategy.check_breakeven(bar)
            # Reentries are handled inside strategy.on_raw_bar() on subsequent bars
            # after the SL hit. Same-bar reentries are blocked by sl_bar_time guard.

        def stream_end_callback(close_price: float, final_time: float):
            trade_manager.close_remaining_trades_at_stream_end(close_price, final_time)

    return combined_bar_callback, stream_end_callback


def _setup_live_mode_callbacks(
    data_source: CombinedDataSource,
    strategy: LiquidityStrategy,
    loader: BarsLoader,
    repos: Repositories,
    pair: str,
    logger: ILogger,
    socketio: SocketIO,
    readiness_state_machine: ReadinessStateMachine,
    history_loaded_deduper: HistoryLoadedDeduper,
):
    """Wire the data source into the readiness state machine."""
    progress_emitter = SocketIOReadinessProgressAdapter(socketio)
    warmup_orchestrator = WarmupOrchestrator(strategy, logger=logger)
    warmup_policy = MinimumBarsWarmupPolicy(min_bars=30)
    bar_buffer = LiveBarBuffer(processor=loader.on_live_bar)
    monitor = ReadinessMonitor(
        state_machine=readiness_state_machine,
        warmup_orchestrator=warmup_orchestrator,
        warmup_policy=warmup_policy,
        bar_buffer=bar_buffer,
        live_bar_processor=loader.on_live_bar,
        data_source=data_source,
        socketio_publisher=socketio,
        history_loaded_emitter=history_loaded_deduper.emit,
        progress_emitter=progress_emitter,
        logger=logger,
    )
    monitor.set_pair(pair)

    def _on_before_refresh():
        """Reset strategy and re-add DB lines before fresh bars arrive."""
        logger.info("[LiveMode] Refresh: resetting strategy...")
        strategy.reset(preserve_trigger_state=True, preserve_histories=True)
        loader.reset()
        for l in repos.lines.list_lines(pair):
            strategy.add_strategy_line(l.line_id, l.price, creation_timestamp=l.creation_date.timestamp())
        logger.info("[LiveMode] Refresh: strategy reset, ready for fresh bars.")

    if isinstance(data_source, ZMQDataSource):
        data_source.on_before_refresh = _on_before_refresh
        data_source.on_refresh_start = monitor.on_refresh_start
        data_source.on_history_complete = monitor.on_history_complete
        data_source.on_live_bar = monitor.on_live_bar
        data_source.on_gap_detected = monitor.on_gap_detected
        data_source.on_heartbeat_stale = monitor.on_heartbeat_stale
        data_source.on_late_history_batch = monitor.on_late_history_batch
        data_source._readiness_monitor = monitor

        if data_source.gateway is not None:
            def _on_gateway_connection_change(connected: bool):
                if connected:
                    readiness_state_machine.connect()
                else:
                    readiness_state_machine.disconnect()
            data_source.gateway.on_connection_change(_on_gateway_connection_change)

    return monitor


def create_app(
    *,
    pair: str,
    data_source: CombinedDataSource,
    repos: Repositories,
    numbers: StrategyNumbers,
    options: Optional[StrategyOptions] = None,
    candle_config: Optional[CandleConfig] = None,
    timeframes: Optional[List[str]] = None,
    strategy_name: str = "liquidity_v2",
    strategy_config=None,
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
    # Ensure standard library loggers emit datetimes in a consistent format.
    configure_logging()

    # Resolve repo root and frontend paths. The app factory lives in backend/,
    # so its parent directory is the repo root.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    frontend_dir = os.path.join(repo_root, "frontend")
    static_dir = os.path.join(frontend_dir, "static")

    app = Flask(__name__, static_folder=static_dir, static_url_path="/static")
    CORS(app)
    # Use threading async mode for better performance with local NinjaTrader
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

    # Resolve platform info once so it can be exposed via /api/config.
    _platform_type = getattr(app_config, "platform_type", "ninjatrader") if app_config else "ninjatrader"
    _platform_label = "NinjaTrader" if _platform_type == "ninjatrader" else "MetaTrader"

    # Wire domain event bus → SocketIO bridge for decoupled notifications
    event_bus = EventBus()
    from src.events.event_bus import SocketIOBridge

    socketio_bridge = SocketIOBridge(socketio, event_bus)
    socketio_bridge.start()

    # Publisher goes through EventBus; SocketIOBridge forwards to SocketIO
    # so every event reaches SocketIO exactly once.
    event_publisher = DomainEventBusPublisher(event_bus)

    _setup_logging(app)

    # Determine the log directory from the supplied logger or app_config.
    _log_dir = getattr(logger, "log_dir", None)
    if _log_dir is None and app_config is not None:
        _log_dir = getattr(app_config, "log_dir", None)
    if not _log_dir:
        _log_dir = "logs"

    # Create the appropriate logger based on mode
    if logger is None:
        if live_mode:
            logger = FileAndConsoleLogger(log_dir=_log_dir)
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
        live_mode=live_mode,
    )

    # Inject trade_manager into executors that are created before trade_manager existed
    if isinstance(trade_executor, MultiAccountExecutor):
        trade_executor.trade_manager = trade_manager
    if isinstance(trade_executor, ZMQTradeExecutor):
        trade_executor.trade_manager = trade_manager
        # Wire async NACK/timeout cleanup back to the executor.
        if isinstance(data_source, ZMQDataSource) and data_source.gateway is not None:
            data_source.gateway.on_command_failed(trade_executor.on_command_failed)

    # Live mode uses a readiness state machine; backtest/replay always trades.
    readiness_state_machine = None
    if live_mode:
        readiness_state_machine = ReadinessStateMachine(
            event_publisher=event_publisher,
            logger=logger,
        )
        execution_context = ReadinessTradingContext(readiness_state_machine)
    else:
        execution_context = AlwaysEnabledTradingContext()

    # Initialize strategy BEFORE registering ZMQ callbacks so closures can reference it safely
    tstrategy = StrategyFactory.create(
        strategy_name,
        min_stop_loss   = numbers.min_stop_loss,
        max_bounce      = getattr(numbers, 'max_bounce', 90.0),
        event_publisher = event_publisher,
        line_repository = repos.lines,
        trade_repository= repos.trades,
        trade_manager   = trade_manager,
        extra_sl_space  = numbers.extra_sl_space,
        fixed_stop_loss = numbers.fixed_stop_loss,
        max_stop_loss   = numbers.max_stop_loss,
        sl_levels       = numbers.sl_levels,
        max_entry_distance = getattr(numbers, 'max_entry_distance', 50.0),
        sl_level_tolerance = getattr(numbers, 'sl_level_tolerance', 3.0),
        min_cross_depth = getattr(numbers, 'min_cross_depth', 5.0),
        rr_ratio        = numbers.rr_ratio,
        point_value     = numbers.point_value,
        account_balance = numbers.account_balance,
        risk_per_trade  = numbers.risk_per_trade,
        risk_pct_per_trade = numbers.risk_pct_per_trade,
        close_on_opposite_cross = getattr(numbers, 'close_on_opposite_cross', False),
        use_fractional_lots = use_fractional_lots,
        fee_per_rt = fee_per_rt,
        broker_spread = broker_spread,
        options         = options,
        config          = strategy_config,
        timeframes      = timeframes,
        candle_config   = candle_config,
        trade_logger    = trade_logger,
        analytics       = analytics,
        trigger_state_repo = repos.trigger_state,
        logger          = logger,
        decision_log_repository = repos.decision_logs,
        account_configs = getattr(numbers, 'account_configs', []),
        accounts_repo=accounts_repo,
        execution_context=execution_context,
        live_mode=live_mode,
    )

    # Wire strategy to TRADE_CLOSED events so it updates state reactively
    # (same code path for live and backtest — broker is source of truth for exits)
    from src.domain.events import EventType
    event_bus.add_subscriber(EventType.TRADE_CLOSED, tstrategy)
    event_bus.add_subscriber(EventType.TRADE_UPDATED, tstrategy)

    # Wire up position sync handler for crash recovery (ZeroMQ only)
    # Broker (NinjaTrader) is the source of truth - it reports actual positions to Python
    if isinstance(data_source, ZMQDataSource) and data_source.gateway is not None:
        def _handle_position_sync(payload):
            """Log broker-reported positions after reconnect.

            The connector no longer recreates Python trades from broker positions.
            NinjaTrader's safety guard is responsible for flattening any position
            that is not properly protected by a stop-loss.
            """
            positions = payload.get('positions', [])
            count = payload.get('count', 0)
            source = payload.get('source', 'unknown')
            untracked = payload.get('untracked_orders', [])

            logger.info(f"📊 POSITION SYNC from {source}: {count} position(s)")
            for pos in positions:
                trade_id = pos.get('trade_id')
                direction = pos.get('direction')
                entry = pos.get('entry_price')
                logger.info(f"   - {trade_id}: {direction} @ {entry}")

            if untracked:
                logger.warning(f"   ⚠️ {len(untracked)} untracked order(s) on broker")
                for order in untracked:
                    logger.warning(f"      - {order.get('order_name')}")

        data_source.gateway.on_position_sync(_handle_position_sync)
        logger.info("[ZMQ] Position sync handler registered for crash recovery")

        # Wire up broker fill handlers so Python chart reflects actual NinjaTrader state
        def _handle_entry_fill(payload):
            trade_id = payload.get('trade_id')
            entry_price = payload.get('entry_price')
            stop_loss = payload.get('stop_loss')
            take_profit = payload.get('take_profit')
            quantity = payload.get('quantity')
            account_balance = payload.get('account_balance')
            if trade_id and entry_price is not None:
                trade_manager.handle_broker_entry_fill(
                    trade_id, entry_price, stop_loss, take_profit, quantity,
                    account_balance=account_balance,
                )
                logger.info(
                    f"[BrokerFill] Entry fill handled for {trade_id} @ {entry_price} "
                    f"qty={quantity} SL={stop_loss} TP={take_profit} balance={account_balance}"
                )

        def _handle_exit_fill(payload):
            trade_id = payload.get('trade_id')
            exit_price = payload.get('exit_price')
            result_type = payload.get('result_type', 'CLOSE')
            broker_pnl_usd = payload.get('realized_pnl')
            broker_fees = payload.get('commission')
            account_balance = payload.get('account_balance')
            if trade_id and exit_price is not None:
                trade_manager.handle_broker_fill(
                    trade_id,
                    exit_price,
                    result_type,
                    broker_pnl_usd=broker_pnl_usd,
                    broker_fees=broker_fees,
                    account_balance=account_balance,
                )
                logger.info(
                    f"[BrokerFill] Exit fill for {trade_id} @ {exit_price} ({result_type}) "
                    f"broker_pnl={broker_pnl_usd}"
                )

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

    # Shared deduper so the readiness monitor and the Socket.IO connect handler
    # do not emit duplicate history_loaded events when the browser reconnects.
    history_loaded_deduper = HistoryLoadedDeduper()

    # In live mode, wire direct callbacks on the data source
    bar_auditor = None
    readiness_monitor = None
    if live_mode:
        readiness_monitor = _setup_live_mode_callbacks(
            data_source, tstrategy, loader, repos, pair, logger, socketio,
            readiness_state_machine, history_loaded_deduper
        )

        # Start background bar auditor to verify NT bars match Python bars
        if isinstance(data_source, ZMQDataSource) and data_source.gateway is not None:
            def _on_bar_drift(result):
                logger.error(f"[CRITICAL] Bar drift detected: {result.summary}")

            bar_auditor = NinjaTraderBarAuditor(
                gateway=data_source.gateway,
                data_source=data_source,
                logger=logger,
                interval_minutes=5,
                bars_back=60,
                on_drift=_on_bar_drift,
            )
            bar_auditor.start()
            logger.info("[LiveMode] BarAuditor started")

            # Create on-demand parity check service
            parity_service = ParityCheckService(
                data_source=data_source,
                gateway=data_source.gateway,
                checker=NinjaTraderParityChecker(
                    market_filter=MarketClosureFilter(instrument=pair),
                ),
                market_filter=MarketClosureFilter(instrument=pair),
                logger=logger,
            )
            logger.info("[LiveMode] ParityCheckService ready")
        else:
            parity_service = None
    else:
        parity_service = None

    # Create controllers
    lines_controller = LinesController(repos.lines, loader, tstrategy, logger=logger)
    trades_controller = TradesController(loader, trade_manager, logger=logger, rr_ratio=numbers.rr_ratio, strategy=tstrategy)

    # Initialize analytics service and admin controller
    analytics_service = AnalyticsService(repos.trades)
    admin_controller = AdminController(
        analytics_service,
        repos.lines,
        logger=logger,
        decision_log_repository=repos.decision_logs,
        log_dir=_log_dir,
    )

    # Initialize settings/manager/NT services from DB
    from src.controllers.settings_controller import SettingsController
    from src.services.nt_manager_service import NtManagerService
    from src.services.platform_deploy_service import PlatformDeployService
    from src.services.settings_service import SettingsService

    secret_key = os.environ.get("SECRET_KEY")
    settings_repo = SettingsRepository(db=db)
    accounts_repo = NtAccountRepository(db=db)
    creds_repo = CredentialRepository(db=db)
    settings_service = SettingsService(settings_repo, accounts_repo, creds_repo, secret_key=secret_key)
    settings_controller = SettingsController(settings_service)
    nt_service = NtManagerService(logger=logger)
    deploy_service = PlatformDeployService()

    # Build platform-specific lifecycle service (SOLID: one implementation per platform)
    if _platform_type == "ninjatrader":
        from src.services.platform_lifecycle.nt_lifecycle_service import (
            NinjaTraderLifecycleService,
        )
        platform_lifecycle = NinjaTraderLifecycleService(nt_service, settings_service, logger)
    else:
        from src.services.mt_manager_service import MetaTraderManagerService
        from src.services.platform_lifecycle.mt_lifecycle_service import (
            MetaTraderLifecycleService,
        )
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
    register_core_routes(
        app, pair, data_source, logger=logger,
        frontend_dir=frontend_dir,
        platform_type=_platform_type,
        platform_label=_platform_label,
    )
    register_lines_routes(app, lines_controller, logger)
    register_trades_routes(app, trades_controller, repos.trades, pair, trade_logger, logger)
    register_admin_routes(app, admin_controller, logger, frontend_dir=frontend_dir)
    register_settings_routes(app, settings_controller, logger)
    register_nt_routes(app, nt_service, deploy_service, logger)
    if _platform_type == "metatrader":
        register_mt_routes(app, mt_service, deploy_service, logger)
    register_stream_routes(app, data_source, platform_lifecycle, socketio, logger)
    register_debug_routes(
        app, tstrategy, loader, trade_manager, repos.lines, repos.trades,
        data_source, pair, notifier, analytics, logger=logger
    )
    register_socketio_handlers(
        socketio, loader, data_source, live_mode, logger, parity_service,
        readiness_monitor=readiness_monitor,
        history_loaded_deduper=history_loaded_deduper,
    )

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
        original = getattr(error, "original_exception", error)
        logger.error("Unhandled server error: %s", original, exc_info=True)
        notifier.send(f"[Flask] Unhandled server error: {original}")
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
        bar_auditor=bar_auditor,
    )
