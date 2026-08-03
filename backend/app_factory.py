# backend/app_factory.py
from __future__ import annotations

import os
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from src.strategies.liquidity_v2.base_strategy import StrategyOptions

from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

from src.analytics import AnalyticsReporter, NoOpReporter
from src.application.stream_coordinator import StreamCoordinator
from src.application.streaming_session import StreamingSession
from src.bars_loader import BarsLoader
from src.config.models import AppConfig
from src.controllers.admin_controller import AdminController
from src.controllers.trades_controller import TradesController
from src.domain.models import Instrument
from src.domain.repositories import (
    LineRepository,
    LineTriggerStateRepository,
    TradeRepository,
)
from src.events.event_bus import EventBus
from src.financial_calc import FinancialCalc
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.event_publisher import (
    DomainEventBusPublisher,
)
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.gateway.executor import MultiAccountExecutor, ZMQTradeExecutor
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
from src.routes.socketio_handlers import SocketIOLogForwarder, make_system_log_forwarder
from src.services.analytics_service import AnalyticsService
from src.services.trade_executor import TradeExecutor
from src.services.trade_logger import TradeLogger
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_v2.base_strategy import StrategyOptions
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.controllers.lines_controller import LinesController
from src.strategies.protocols import LiquidityStrategy
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
    admin_controller: AdminController
    data_source: CombinedDataSource
    pair: str
    live_mode: bool = False
    logger: Optional[ILogger] = None
    coordinator: StreamCoordinator | None = None

    def _session(self) -> StreamingSession:
        """Resolve this wiring's explicit pair to its streaming session.

        The legacy single-instrument accessors below are thin shims over the
        coordinator; the session is created lazily on first access.  Nothing
        here subscribes the instrument — subscriptions happen only via
        ``join_instrument``.
        """
        if self.coordinator is None:
            raise RuntimeError("AppWiring has no StreamCoordinator")
        return self.coordinator.require_session(self.pair)

    @property
    def loader(self) -> BarsLoader:
        return self._session().bars_loader

    @property
    def strategy(self) -> LiquidityStrategy:
        return self._session().strategy

    @property
    def trade_manager(self) -> TradeManager:
        return self._session().trade_manager

    @property
    def lines_controller(self) -> LinesController:
        return self._session().lines_controller

    @property
    def trades_controller(self) -> TradesController:
        return self._session().trades_controller

    @property
    def readiness_monitor(self):
        return self._session().readiness_monitor

    @property
    def parity_service(self):
        return self._session().parity_service

    @property
    def bar_auditor(self) -> Any | None:
        return self._session().bar_auditor


def _setup_logging(app: Flask):
    """Configure logging filters for high-frequency endpoints."""
    import logging
    class _QuietFilter(logging.Filter):
        _NOISY = ('/api/nt/tick', '/api/nt/partial')
        def filter(self, record):
            msg = record.getMessage()
            return not any(p in msg for p in self._NOISY)
    logging.getLogger('werkzeug').addFilter(_QuietFilter())


class _SessionFactory:
    """Creates ``StreamingSession`` instances for the ``StreamCoordinator``."""

    def __init__(self, session_params_provider=None, **kwargs: Any):
        # Optional callable ``(symbol) -> (StrategyNumbers, StrategyOptions)``
        # providing per-instrument parameters; absent it, all sessions share
        # the ``numbers``/``options`` passed in ``kwargs`` (legacy behavior).
        self._params_provider = session_params_provider
        self._kwargs = kwargs

    def create_session(self, instrument: Instrument) -> StreamingSession:
        kwargs = self._kwargs
        if self._params_provider is not None:
            numbers, options = self._params_provider(instrument.symbol)
            kwargs = {**kwargs, "numbers": numbers, "options": options}
        return StreamingSession(instrument=instrument, **kwargs)


class _SingleInstrumentRegistry:
    """Backward-compatible registry containing exactly one instrument."""

    def __init__(self, symbol: str, full_name: str):
        self._instrument = Instrument(symbol=symbol, full_name=full_name)

    def get_all(self) -> list[Instrument]:
        return [self._instrument]

    def save(self, instruments: list[Instrument]) -> None:
        pass


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
    app_config: AppConfig | None = None,
    instrument_registry=None,
    session_params_provider: "Callable[[str], tuple[StrategyNumbers, StrategyOptions]] | None" = None,
    session_end_time: str | None = None,
    session_tz: str = "America/New_York",
    warmup_min_bars: int = 30,
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
    _platform_type = app_config.platform_type if app_config else "ninjatrader"
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
    _log_dir = logger.log_dir if isinstance(logger, FileAndConsoleLogger) else None
    if _log_dir is None and app_config is not None:
        _log_dir = app_config.log_dir
    if not _log_dir:
        _log_dir = "logs"

    # Create the appropriate logger based on mode
    if logger is None:
        if live_mode:
            logger = FileAndConsoleLogger(log_dir=_log_dir)
        else:
            logger = ConsoleLogger()

    # Decorate the shared logger eagerly so every component wired below
    # (sessions, controllers, routes) forwards info/warning/error logs to
    # connected browsers. register_socketio_handlers keeps this idempotent.
    if not isinstance(logger, SocketIOLogForwarder):
        logger = SocketIOLogForwarder(logger, make_system_log_forwarder(socketio))

    if notifier is None:
        notifier = NoOpNotifier()
    if analytics is None:
        analytics = NoOpReporter()
    analytics.set_context("app", {"pair": pair, "live_mode": live_mode})

    trade_logger = TradeLogger(repos.trades)

    if accounts_repo is None and db is not None:
        accounts_repo = NtAccountRepository(db=db)

    _resolved_session_end = session_end_time or (app_config.session_end if app_config else None) or "16:58"

    # Resolve the instrument registry.  For backward compatibility, fall back to a
    # single-instrument registry built from the configured pair.
    if instrument_registry is None:
        registry = _SingleInstrumentRegistry(symbol=pair, full_name=pair)
    else:
        registry = instrument_registry

    # Shared deduper so the readiness monitor and the Socket.IO connect handler
    # do not emit duplicate history_loaded events when the browser reconnects.
    history_loaded_deduper = HistoryLoadedDeduper()

    # Factory that builds per-instrument sessions with the same dependencies.
    session_factory = _SessionFactory(
        session_params_provider=session_params_provider,
        socketio=socketio,
        data_source=data_source,
        repos=repos,
        numbers=numbers,
        options=options,
        candle_config=candle_config,
        timeframes=timeframes,
        strategy_name=strategy_name,
        strategy_config=strategy_config,
        broker_mode=broker_mode,
        broker_spread=broker_spread,
        use_fractional_lots=use_fractional_lots,
        fee_per_rt=fee_per_rt,
        live_mode=live_mode,
        trade_executor=trade_executor,
        notifier=notifier,
        analytics=analytics,
        logger=logger,
        db=db,
        accounts_repo=accounts_repo,
        session_end_time=_resolved_session_end,
        session_tz=session_tz,
        event_bus=event_bus,
        bootstrap_existing_lines=bootstrap_existing_lines,
        history_loaded_deduper=history_loaded_deduper,
        warmup_min_bars=warmup_min_bars,
    )

    coordinator = StreamCoordinator(
        instrument_registry=registry,
        session_factory=session_factory,
        stream_activator=(
            data_source.ensure_instrument_streaming
            if isinstance(data_source, ZMQDataSource)
            else None
        ),
    )

    # Wire the data source to route incoming market data by pair.
    if isinstance(data_source, ZMQDataSource):
        data_source.set_coordinator(coordinator)

    # ------------------------------------------------------------------
    # Cross-session trade resolution for the shared trade executor.
    # One executor serves every instrument session, so close/modify/cancel
    # commands resolve their trade (instrument, account) across all active
    # sessions instead of a per-session TradeManager reference.
    # ------------------------------------------------------------------
    def _full_name_for_symbol(symbol: str | None) -> str | None:
        if not symbol:
            return None
        for instrument in registry.get_all():
            if instrument.symbol == symbol:
                return instrument.full_name
        return None

    def _resolve_trade_across_sessions(trade_id: str) -> dict | None:
        for session in coordinator.get_active_sessions():
            for trade in session.trade_manager.open_trades:
                if trade.get("trade_id") == trade_id:
                    return trade
        record = repos.trades.get_trade(trade_id)
        if record is None:
            return None
        return {
            "trade_id": record.trade_id,
            "pair": record.pair,
            "account": record.account,
            "instrument": _full_name_for_symbol(record.pair),
        }

    def _cancel_trade_across_sessions(trade_id: str, reason: str) -> None:
        for session in coordinator.get_active_sessions():
            trade_manager = session.trade_manager
            if any(t.get("trade_id") == trade_id for t in trade_manager.open_trades):
                trade_manager.cancel_trade(trade_id, reason=reason)
                return
        # Not found in any session — cancel via an arbitrary session's manager
        # (cancellation is repository-backed, so any session works).
        sessions = coordinator.get_active_sessions()
        if sessions:
            sessions[0].trade_manager.cancel_trade(trade_id, reason=reason)

    gateway_executor = (
        trade_executor.gateway_executor
        if isinstance(trade_executor, MultiAccountExecutor)
        else trade_executor
    )
    if isinstance(gateway_executor, ZMQTradeExecutor):
        gateway_executor.trade_resolver = _resolve_trade_across_sessions
        gateway_executor.trade_canceler = _cancel_trade_across_sessions

    # Wire async NACK/timeout cleanup back to the executor.
    if isinstance(gateway_executor, ZMQTradeExecutor):
        if isinstance(data_source, ZMQDataSource) and data_source.gateway is not None:
            data_source.gateway.on_command_failed(gateway_executor.on_command_failed)

    # Forward gateway connection changes to every session's readiness state machine.
    if isinstance(data_source, ZMQDataSource) and data_source.gateway is not None:
        def _on_gateway_connection_change(connected: bool):
            for session in coordinator.get_active_sessions():
                monitor = session.readiness_monitor
                if monitor is None:
                    continue
                monitor.on_connection_change(connected)
        data_source.gateway.on_connection_change(_on_gateway_connection_change)

    # Wire up position sync / broker fill handlers (ZeroMQ only).
    # Fills are routed to the session that owns the trade: first by the
    # instrument/pair reported in the payload, then by searching every active
    # session for the trade id.
    if isinstance(data_source, ZMQDataSource) and data_source.gateway is not None:
        def _symbol_for_payload(payload) -> str | None:
            """Resolve a payload's instrument full_name or pair to a registry symbol."""
            reported = payload.get("instrument") or payload.get("pair")
            if not reported:
                return None
            for instrument in registry.get_all():
                if reported in (instrument.full_name, instrument.symbol):
                    return instrument.symbol
            return reported if coordinator.get_session(reported) is not None else None

        def _session_for_trade(payload, trade_id) -> StreamingSession | None:
            symbol = _symbol_for_payload(payload)
            if symbol is not None:
                session = coordinator.get_session(symbol)
                if session is not None:
                    return session
            for session in coordinator.get_active_sessions():
                if any(t.get("trade_id") == trade_id for t in session.trade_manager.open_trades):
                    return session
            # Last resort: the DB record knows which pair the trade belongs to.
            record = repos.trades.get_trade(trade_id)
            if record is not None and record.pair:
                return coordinator.get_or_create_session(record.pair)
            return None

        def _handle_position_sync(payload):
            """Log broker-reported positions after reconnect."""
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

        def _handle_entry_fill(payload):
            trade_id = payload.get('trade_id')
            entry_price = payload.get('entry_price')
            stop_loss = payload.get('stop_loss')
            take_profit = payload.get('take_profit')
            quantity = payload.get('quantity')
            account_balance = payload.get('account_balance')
            if trade_id and entry_price is not None:
                session = _session_for_trade(payload, trade_id)
                if session is None:
                    logger.error(
                        f"[BrokerFill] Entry fill for unknown trade {trade_id} — no session owns it; dropped"
                    )
                    return
                session.trade_manager.handle_broker_entry_fill(
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
                session = _session_for_trade(payload, trade_id)
                if session is None:
                    logger.error(
                        f"[BrokerFill] Exit fill for unknown trade {trade_id} — no session owns it; dropped"
                    )
                    return
                session.trade_manager.handle_broker_fill(
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
    nt_accounts_repo = NtAccountRepository(db=db)
    creds_repo = CredentialRepository(db=db)
    settings_service = SettingsService(
        settings_repo,
        nt_accounts_repo,
        creds_repo,
        secret_key=secret_key,
        instrument_registry=registry,
    )
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

    # Register routes.  Per-instrument controllers are resolved from the
    # coordinator by explicit pair (see lines/trades/debug routes); there is
    # no default session to fall back to.
    register_core_routes(
        app, pair, data_source, logger=logger,
        frontend_dir=frontend_dir,
        platform_type=_platform_type,
        platform_label=_platform_label,
        settings_service=settings_service,
        mode=app_config.mode if app_config else "live",
        csv_file=app_config.csv_file if app_config else None,
        start_str=app_config.start_str if app_config else None,
        end_str=app_config.end_str if app_config else None,
        bars_per_second=app_config.bars_per_second if app_config else None,
    )
    register_lines_routes(app, None, logger, coordinator=coordinator, lines_repo=repos.lines)
    register_trades_routes(app, None, repos.trades, pair, trade_logger, logger, coordinator=coordinator)
    register_admin_routes(app, admin_controller, logger, frontend_dir=frontend_dir)
    register_settings_routes(app, settings_controller, logger)
    register_nt_routes(app, nt_service, deploy_service, logger)
    if _platform_type == "metatrader":
        register_mt_routes(app, mt_service, deploy_service, logger)
    register_stream_routes(app, data_source, platform_lifecycle, socketio, logger, coordinator=coordinator)
    register_debug_routes(
        app, None, None, None, repos.lines, repos.trades,
        data_source, pair, notifier, analytics, logger=logger, coordinator=coordinator,
    )
    register_socketio_handlers(
        socketio, None, data_source, live_mode, logger, None,
        readiness_monitor=None,
        history_loaded_deduper=history_loaded_deduper,
        coordinator=coordinator,
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
        original = error.original_exception or error
        logger.error(f"Unhandled server error: {original}\n{traceback.format_exc()}")
        notifier.send(f"[Flask] Unhandled server error: {original}")
        return jsonify({"error": "Internal server error"}), 500

    return AppWiring(
        app=app,
        socketio=socketio,
        admin_controller=admin_controller,
        data_source=data_source,
        pair=pair,
        live_mode=live_mode,
        logger=logger,
        coordinator=coordinator,
    )
