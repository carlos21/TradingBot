"""Per-instrument streaming session.

A ``StreamingSession`` encapsulates all state and processing for a single
instrument: its own ``BarsLoader``, strategy instance, ``TradeManager``,
controllers, and live-mode readiness monitor.  It emits Socket.IO events only
to the room named after the instrument symbol so that browser tabs receive
data only for their selected instrument.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any

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
from src.controllers.trades_controller import TradesController
from src.domain.models import Instrument
from src.domain.readiness import ReadinessStateMachine
from src.events.event_bus import EventBus
from src.financial_calc import FinancialCalc
from src.infrastructure.bar_auditor import NinjaTraderBarAuditor
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.event_publisher import DomainEventBusPublisher
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.market_closure_filter import MarketClosureFilter
from src.infrastructure.parity_checker import NinjaTraderParityChecker
from src.infrastructure.readiness_progress_adapter import (
    SocketIOReadinessProgressAdapter,
)
from src.infrastructure.repositories.accounts_repository import NtAccountRepository
from src.notifier import NoOpNotifier, Notifier
from src.services.analytics_service import AnalyticsService
from src.services.trade_executor import TradeExecutor
from src.services.trade_logger import TradeLogger
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_v2.base_strategy import StrategyOptions
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.controllers.lines_controller import LinesController
from src.strategies.protocols import LiquidityStrategy
from src.strategies.strategy_factory import StrategyFactory
from src.utils.app_logger import ILogger
from src.utils.history_loaded_deduper import HistoryLoadedDeduper


class StreamingSession:
    """Container for all per-instrument streaming state and callbacks.

    ``repos`` is expected to be the ``Repositories`` dataclass defined in
    ``app_factory`` (passed as ``Any`` here to avoid a circular import).
    """

    def __init__(
        self,
        instrument: Instrument,
        socketio: Any,
        data_source: CombinedDataSource,
        repos: Any,
        numbers: StrategyNumbers,
        options: StrategyOptions | None = None,
        candle_config: CandleConfig | None = None,
        timeframes: list[str] | None = None,
        strategy_name: str = "liquidity_v2",
        strategy_config=None,
        broker_mode: str = "futures",
        broker_spread: float = 0.0,
        use_fractional_lots: bool = False,
        fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
        live_mode: bool = False,
        trade_executor: TradeExecutor | None = None,
        notifier: Notifier | None = None,
        analytics: AnalyticsReporter | None = None,
        logger: ILogger | None = None,
        db: DatabaseProtocol | None = None,
        accounts_repo=None,
        session_end_time: str | None = None,
        session_tz: str = "America/New_York",
        event_bus: EventBus | None = None,
        bootstrap_existing_lines: bool = True,
        history_loaded_deduper: HistoryLoadedDeduper | None = None,
    ):
        self.instrument = instrument
        self.symbol = instrument.symbol
        self._socketio = socketio
        self._data_source = data_source
        self._repos = repos
        self._numbers = numbers
        self._options = options
        self._candle_config = candle_config
        self._timeframes = timeframes
        self._strategy_name = strategy_name
        self._strategy_config = strategy_config
        self._broker_mode = broker_mode
        self._broker_spread = broker_spread
        self._use_fractional_lots = use_fractional_lots
        self._fee_per_rt = fee_per_rt
        self._live_mode = live_mode
        self._trade_executor = trade_executor
        self._notifier = notifier or NoOpNotifier()
        self._analytics = analytics or NoOpReporter()
        self._analytics.set_context("app", {"pair": instrument.symbol, "live_mode": live_mode})
        self._logger = logger
        self._db = db
        self._accounts_repo = accounts_repo
        self._session_end_time = session_end_time
        self._session_tz = session_tz
        self._event_bus = event_bus
        self._bootstrap_existing_lines = bootstrap_existing_lines
        self._history_loaded_deduper = history_loaded_deduper

        # Create the shared readiness state machine early so the strategy and
        # the readiness monitor both observe the same instance.
        self._readiness_state_machine = None
        if self._live_mode:
            self._readiness_state_machine = ReadinessStateMachine(
                event_publisher=self._event_publisher(),
                logger=self._logger,
            )

        self._lock = threading.RLock()
        self._clients: set[str] = set()
        self._started = False

        # Build components
        self.trade_logger = TradeLogger(repos.trades)
        self.trade_manager = self._build_trade_manager()
        self.strategy = self._build_strategy()
        self.bars_loader = self._build_bars_loader()
        self.lines_controller = self._build_lines_controller()
        self.trades_controller = self._build_trades_controller()
        self.readiness_monitor = self._build_readiness_monitor()
        self.bar_auditor = self._build_bar_auditor()
        self.parity_service = self._build_parity_service()

    # ------------------------------------------------------------------
    # Builders (extracted from app_factory for per-instrument isolation)
    # ------------------------------------------------------------------

    def _resolve_accounts_repo(self):
        if self._accounts_repo is not None:
            return self._accounts_repo
        if self._db is not None:
            return NtAccountRepository(db=self._db)
        return None

    def _build_trade_manager(self) -> TradeManager:
        accounts_repo = self._resolve_accounts_repo()
        session_end = self._session_end_time or "16:58"

        tm = TradeManager(
            trade_repository=self._repos.trades,
            socketio=self._event_publisher(),
            pair=self.symbol,
            session_end_time=session_end,
            session_tz=self._session_tz,
            broker_mode=self._broker_mode,
            broker_spread=self._broker_spread,
            use_fractional_lots=self._use_fractional_lots,
            fee_per_rt=self._fee_per_rt,
            trade_executor=self._trade_executor,
            trade_logger=self.trade_logger,
            notifier=self._notifier,
            analytics=self._analytics,
            point_value=self._numbers.point_value,
            account_balance=self._numbers.account_balance,
            risk_per_trade=self._numbers.risk_per_trade,
            risk_pct_per_trade=self._numbers.risk_pct_per_trade,
            be_threshold_points=self._numbers.be_threshold_points,
            sl_tp_tolerance=self._numbers.sl_tp_tolerance,
            logger=self._logger,
            accounts_repo=accounts_repo,
            instrument=self.instrument.full_name,
            live_mode=self._live_mode,
        )
        return tm

    def _build_strategy(self) -> LiquidityStrategy:
        execution_context = self._execution_context()

        strategy = StrategyFactory.create(
            self._strategy_name,
            min_stop_loss=self._numbers.min_stop_loss,
            max_bounce=self._numbers.max_bounce,
            event_publisher=self._event_publisher(),
            line_repository=self._repos.lines,
            trade_repository=self._repos.trades,
            trade_manager=self.trade_manager,
            extra_sl_space=self._numbers.extra_sl_space,
            fixed_stop_loss=self._numbers.fixed_stop_loss,
            max_stop_loss=self._numbers.max_stop_loss,
            sl_levels=self._numbers.sl_levels,
            max_entry_distance=self._numbers.max_entry_distance,
            sl_level_tolerance=self._numbers.sl_level_tolerance,
            min_cross_depth=self._numbers.min_cross_depth,
            rr_ratio=self._numbers.rr_ratio,
            point_value=self._numbers.point_value,
            account_balance=self._numbers.account_balance,
            risk_per_trade=self._numbers.risk_per_trade,
            risk_pct_per_trade=self._numbers.risk_pct_per_trade,
            close_on_opposite_cross=self._numbers.close_on_opposite_cross,
            be_threshold_points=self._numbers.be_threshold_points,
            sl_tp_tolerance=self._numbers.sl_tp_tolerance,
            use_fractional_lots=self._use_fractional_lots,
            fee_per_rt=self._fee_per_rt,
            broker_spread=self._broker_spread,
            options=self._options,
            config=self._strategy_config,
            timeframes=self._timeframes,
            candle_config=self._candle_config,
            trade_logger=self.trade_logger,
            analytics=self._analytics,
            trigger_state_repo=self._repos.trigger_state,
            logger=self._logger,
            decision_log_repository=self._repos.decision_logs,
            account_configs=self._numbers.account_configs,
            accounts_repo=self._resolve_accounts_repo(),
            execution_context=execution_context,
            live_mode=self._live_mode,
        )

        if self._event_bus is not None:
            from src.domain.events import EventType

            self._event_bus.add_subscriber(EventType.TRADE_CLOSED, strategy)
            self._event_bus.add_subscriber(EventType.TRADE_UPDATED, strategy)

        if self._bootstrap_existing_lines:
            for line in self._repos.lines.list_lines(self.symbol):
                strategy.add_strategy_line(
                    line.line_id,
                    line.price,
                    creation_timestamp=line.creation_date.timestamp(),
                )
            if self._live_mode:
                strategy.restore_open_trades()

        return strategy

    def _build_bars_loader(self) -> BarsLoader:
        bar_callback, stream_end_callback = self._create_bar_callbacks()

        loader = BarsLoader(
            data_source=self._data_source,
            socketio=self._socketio,
            bar_callback=bar_callback,
            stream_end_callback=stream_end_callback,
            logger=self._logger,
            room=self.symbol,
            pair=self.symbol,
        )
        loader.live_mode = self._live_mode

        # Re-create callbacks now that loader exists (needed for live session-end logic)
        bar_callback, stream_end_callback = self._create_bar_callbacks(loader)
        loader.bar_callback = bar_callback
        loader.stream_end_callback = stream_end_callback
        return loader

    def _build_lines_controller(self) -> LinesController:
        return LinesController(
            self._repos.lines,
            self.bars_loader,
            self.strategy,
            logger=self._logger,
        )

    def _build_trades_controller(self) -> TradesController:
        return TradesController(
            self.bars_loader,
            self.trade_manager,
            logger=self._logger,
            rr_ratio=self._numbers.rr_ratio,
            strategy=self.strategy,
        )

    def _build_readiness_monitor(self) -> ReadinessMonitor | None:
        if not self._live_mode:
            return None

        readiness_state_machine = self._readiness_state_machine
        if readiness_state_machine is None:
            readiness_state_machine = ReadinessStateMachine(
                event_publisher=self._event_publisher(),
                logger=self._logger,
            )
            self._readiness_state_machine = readiness_state_machine

        progress_emitter = SocketIOReadinessProgressAdapter(self._socketio)
        warmup_orchestrator = WarmupOrchestrator(self.strategy, logger=self._logger)
        warmup_policy = MinimumBarsWarmupPolicy(min_bars=30)
        bar_buffer = LiveBarBuffer(processor=self.bars_loader.on_live_bar)

        monitor = ReadinessMonitor(
            state_machine=readiness_state_machine,
            warmup_orchestrator=warmup_orchestrator,
            warmup_policy=warmup_policy,
            bar_buffer=bar_buffer,
            live_bar_processor=self.bars_loader.on_live_bar,
            data_source=self._data_source,
            socketio_publisher=self._socketio,
            history_loaded_emitter=self._history_loaded_deduper.emit if self._history_loaded_deduper else None,
            progress_emitter=progress_emitter,
            logger=self._logger,
            history_bars_provider=lambda: self._data_source.load_historical_bars(pair=self.symbol),
        )
        monitor.set_pair(self.symbol)
        return monitor

    def _build_bar_auditor(self) -> NinjaTraderBarAuditor | None:
        if not self._live_mode:
            return None
        if not isinstance(self._data_source, ZMQDataSource):
            return None
        gateway = self._data_source.gateway
        if gateway is None:
            return None

        def _on_bar_drift(result):
            if self._logger:
                self._logger.error(f"[CRITICAL] Bar drift detected: {result.summary}")

        auditor = NinjaTraderBarAuditor(
            gateway=gateway,
            data_source=self._data_source,
            logger=self._logger,
            interval_minutes=5,
            bars_back=60,
            pair=self.symbol,
            instrument=self.instrument.full_name,
            on_drift=_on_bar_drift,
        )
        auditor.start()
        if self._logger:
            self._logger.info("[LiveMode] BarAuditor started")
        return auditor

    def _build_parity_service(self) -> ParityCheckService | None:
        if not self._live_mode:
            return None
        if not isinstance(self._data_source, ZMQDataSource):
            return None
        gateway = self._data_source.gateway
        if gateway is None:
            return None

        market_filter = MarketClosureFilter(instrument=self.symbol)
        parity_service = ParityCheckService(
            data_source=self._data_source,
            gateway=gateway,
            checker=NinjaTraderParityChecker(market_filter=market_filter),
            market_filter=market_filter,
            logger=self._logger,
            pair=self.symbol,
            instrument=self.instrument.full_name,
        )
        if self._logger:
            self._logger.info("[LiveMode] ParityCheckService ready")
        return parity_service

    def _event_publisher(self):
        if self._event_bus is not None:
            return DomainEventBusPublisher(self._event_bus)
        return self._socketio

    def _execution_context(self):
        if not self._live_mode:
            return AlwaysEnabledTradingContext()
        if self._readiness_state_machine is None:
            self._readiness_state_machine = ReadinessStateMachine(
                event_publisher=self._event_publisher(),
                logger=self._logger,
            )
        return ReadinessTradingContext(self._readiness_state_machine)

    def _create_bar_callbacks(self, loader: BarsLoader | None = None):
        """Create (bar_callback, stream_end_callback) for this instrument."""
        trade_manager = self.trade_manager
        strategy = self.strategy

        if self._live_mode:
            _close_commands_sent: set = set()

            def _check_live_session_end(bar):
                if not trade_manager._session_end_time or not trade_manager._session_tz:
                    return
                from datetime import datetime

                bar_dt = datetime.fromtimestamp(bar["time"], tz=trade_manager._session_tz)
                if bar_dt.time() < trade_manager._session_end_time:
                    return
                for t in list(trade_manager.open_trades):
                    if t["pair"] != bar["pair"] or t["entry_time"] > bar["time"]:
                        continue
                    if t.get("source") in trade_manager.USER_CONTROLLED_SOURCES:
                        continue
                    tid = t["trade_id"]
                    if tid not in _close_commands_sent:
                        if self._logger:
                            self._logger.info(
                                f"[LiveMode] SESSION END — sending close_order to NT for {tid}"
                            )
                        trade_manager.trade_logger.log(tid, "SESSION_END", "Sending close_order to NinjaTrader")
                        trade_manager.trade_logger.log(tid, "CMD_SENT", "close_order → NinjaTrader")
                        trade_manager.trade_executor.on_trade_close(tid, bar["close"])
                        _close_commands_sent.add(tid)

            def combined_bar_callback(bar):
                strategy.on_raw_bar(bar)
                if strategy.options.breakeven or strategy.options.reentry_breakeven:
                    strategy.check_breakeven(bar)
                _check_live_session_end(bar)

            def stream_end_callback(close_price: float, final_time: float):
                for t in list(trade_manager.open_trades):
                    tid = t["trade_id"]
                    if tid not in _close_commands_sent:
                        if self._logger:
                            self._logger.info(f"[LiveMode] STREAM END — sending close_order to NT for {tid}")
                        trade_manager.trade_logger.log(tid, "SESSION_END", "Stream end — sending close_order to NinjaTrader")
                        trade_manager.trade_logger.log(tid, "CMD_SENT", "close_order → NinjaTrader")
                        trade_manager.trade_executor.on_trade_close(tid, close_price)
                        _close_commands_sent.add(tid)
        else:
            def combined_bar_callback(bar):
                strategy.on_raw_bar(bar)
                trade_manager.handle_new_1m_bar(bar)
                if strategy.options.breakeven or strategy.options.reentry_breakeven:
                    strategy.check_breakeven(bar)

            def stream_end_callback(close_price: float, final_time: float):
                trade_manager.close_remaining_trades_at_stream_end(close_price, final_time)

        return combined_bar_callback, stream_end_callback

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start processing data for this instrument."""
        with self._lock:
            if self._started:
                return
            self._started = True

        # Seed the readiness monitor with the current gateway connection state:
        # a session created mid-stream (platform already connected) must not sit
        # in DISCONNECTED silently dropping live bars until its first refresh.
        if (
            self.readiness_monitor is not None
            and isinstance(self._data_source, ZMQDataSource)
            and self._data_source.is_connected
        ):
            with contextlib.suppress(Exception):
                self.readiness_monitor.on_connection_change(True)

        if self._logger:
            self._logger.info(f"[StreamingSession] Started session for {self.symbol}")

    def stop(self) -> None:
        """Stop processing data for this instrument."""
        with self._lock:
            if not self._started:
                return
            self._started = False

        if self.readiness_monitor is not None:
            with contextlib.suppress(Exception):
                self.readiness_monitor.stop()
        if self.bar_auditor is not None:
            with contextlib.suppress(Exception):
                self.bar_auditor.stop()

        if self._logger:
            self._logger.info(f"[StreamingSession] Stopped session for {self.symbol}")

    def join_client(self, sid: str) -> None:
        """Track a client that is viewing this instrument."""
        with self._lock:
            self._clients.add(sid)

    def leave_client(self, sid: str) -> None:
        """Remove a client from this instrument."""
        with self._lock:
            self._clients.discard(sid)

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    # ------------------------------------------------------------------
    # Data callbacks (routed by coordinator)
    # ------------------------------------------------------------------

    def on_bar(self, bar: dict) -> None:
        """Handle a completed bar for this instrument."""
        if self.readiness_monitor is not None:
            self.readiness_monitor.on_live_bar(bar)
        else:
            self.bars_loader.on_live_bar(bar)

    def on_tick(self, tick: dict) -> None:
        """Handle a tick for this instrument."""
        # Ticks update the chart only; route through bars_loader so partial
        # bars and ticks are emitted to the instrument room.
        self.bars_loader.on_live_bar(tick)

    def on_partial_bar(self, partial: dict) -> None:
        """Handle a partial bar for this instrument."""
        if self.readiness_monitor is not None:
            self.readiness_monitor.on_live_bar(partial)
        else:
            self.bars_loader.on_live_bar(partial)

    def on_history_loaded(self, bars: list[dict]) -> None:
        """Handle a completed historical batch for this instrument."""
        if self.readiness_monitor is not None:
            self.readiness_monitor.on_history_complete(bars)

    def on_refresh_start(self) -> None:
        """Handle a historical refresh start for this instrument."""
        if self.readiness_monitor is not None:
            self.readiness_monitor.on_refresh_start()
        else:
            self.strategy.reset(preserve_trigger_state=True, preserve_histories=True)
            self.bars_loader.reset()
            for line in self._repos.lines.list_lines(self.symbol):
                self.strategy.add_strategy_line(
                    line.line_id, line.price, creation_timestamp=line.creation_date.timestamp()
                )

    def on_before_refresh(self) -> None:
        """Reset strategy state before fresh bars arrive."""
        if self._logger:
            self._logger.info(f"[LiveMode] Refresh: resetting strategy for {self.symbol}...")
        self.strategy.reset(preserve_trigger_state=True, preserve_histories=True)
        self.bars_loader.reset()
        for line in self._repos.lines.list_lines(self.symbol):
            self.strategy.add_strategy_line(
                line.line_id, line.price, creation_timestamp=line.creation_date.timestamp()
            )
        if self._logger:
            self._logger.info(f"[LiveMode] Refresh: strategy reset for {self.symbol}, ready for fresh bars.")

    def on_gap_detected(self, gap_seconds: int, context: str) -> None:
        if self.readiness_monitor is not None:
            self.readiness_monitor.on_gap_detected(gap_seconds, context)

    def on_heartbeat_stale(self, age_seconds: float) -> None:
        if self.readiness_monitor is not None:
            self.readiness_monitor.on_heartbeat_stale(age_seconds)

    def on_late_history_batch(self, bar_count: int) -> None:
        if self.readiness_monitor is not None:
            self.readiness_monitor.on_late_history_batch(bar_count)

    # ------------------------------------------------------------------
    # Room-scoped emit helpers
    # ------------------------------------------------------------------

    def emit(self, event: str, payload: dict) -> None:
        """Emit a Socket.IO event to this instrument's room."""
        try:
            self._socketio.emit(event, payload, room=self.symbol)
        except Exception as exc:
            if self._logger:
                self._logger.error(f"[StreamingSession] emit {event} failed: {exc}")
