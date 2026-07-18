"""Application builder — turns ``AppConfig`` into a fully wired ``AppWiring``.

This module replaces the monolithic ``build_prod()`` / ``build_live()``
functions that previously lived in ``app.py``. It follows SRP by
containing *only* the "how do I assemble the app?" logic.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy.exc import OperationalError

if TYPE_CHECKING:
    from app_factory import AppWiring

from src.analytics import AnalyticsReporter, NoOpReporter, SentryReporter
from src.config.models import AppConfig
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.infrastructure.data_sources.csv_datasource import CSVDataSource
from src.infrastructure.database import database
from src.infrastructure.database.database_protocol import get_database
from src.infrastructure.gateway import (
    create_live_components,
    create_multi_account_live_components,
)
from src.infrastructure.repositories.accounts_repository import NtAccountRepository
from src.infrastructure.repositories.line_trigger_state_repository import (
    SQLiteLineTriggerStateRepository,
)
from src.infrastructure.repositories.lines_repository import SQLLineRepository
from src.infrastructure.repositories.settings_repository import SettingsRepository
from src.infrastructure.repositories.trades_repository import SQLTradeRepository
from src.notifier import NoOpNotifier, Notifier, TelegramNotifier
from src.services.instrument_registry import InstrumentRegistry
from src.strategies.liquidity_v2.config import StrategyNumbers
from src.strategies.liquidity_v2.base_strategy import StrategyOptions
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from src.strategies.liquidity_v2.instrument_params import HardcodedInstrumentCatalog
from src.strategies.liquidity_v2.prod_config import (
    get_prod_candle_config,
    get_prod_strategy_numbers,
    get_prod_strategy_options,
)
from src.utils.app_logger import FileAndConsoleLogger, configure_logging, log_timestamp


def _parse_input_to_epoch(date_str: str, tz_name: str) -> int | None:
    """Parse a date string using the specified timezone, return UTC epoch."""
    if not date_str:
        return None
    input_tz = ZoneInfo(tz_name)
    try:
        dt = datetime.fromisoformat(date_str)
    except ValueError:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=input_tz)
    return int(dt.timestamp())


def _build_analytics(config: AppConfig) -> AnalyticsReporter:
    if config.sentry_dsn:
        print(f"{log_timestamp()} [App] Sentry analytics enabled")
        return SentryReporter(config.sentry_dsn)
    return NoOpReporter()


def _build_notifier(config: AppConfig) -> Notifier:
    if config.telegram_token and config.telegram_chat_id:
        print(f"{log_timestamp()} [App] Telegram notifications enabled")
        return TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    return NoOpNotifier()


def _build_database(cfg: AppConfig):
    """Create and set up the app database, falling back to SQLite.

    When ``database_url`` points at a server that cannot be reached
    (e.g. PostgreSQL is not running on this machine), fall back to the
    local SQLite ``db_path`` instead of aborting startup.
    """
    try:
        db = get_database(db_url=cfg.database_url or cfg.db_path)
        database.setup_database(database_instance=db)
        return db
    except OperationalError:
        if not cfg.database_url or cfg.database_url == cfg.db_path:
            raise
        print(f"{log_timestamp()} [App] Database unreachable at {cfg.database_url}; "
              f"falling back to SQLite: {cfg.db_path}")
        db = get_database(db_url=cfg.db_path)
        database.setup_database(database_instance=db)
        return db


class AppBuilder:
    """Assemble the application from a configuration object."""

    def __init__(self, config: AppConfig):
        self.config = config

    def build(self) -> tuple[AppWiring, CombinedDataSource | None]:
        # Ensure standard-library loggers emit datetimes before any data source
        # or gateway code starts logging.
        configure_logging()
        if self.config.mode == "live":
            return self._build_live()
        return self._build_backtest(), None

    # ------------------------------------------------------------------
    # Instrument stack (composition root for per-instrument parameters)
    # ------------------------------------------------------------------
    def _build_instrument_stack(
        self, db
    ) -> tuple[InstrumentRegistry, Callable[[str], tuple[StrategyNumbers, StrategyOptions]]]:
        """Build the instrument registry and the per-symbol params provider.

        Every instrument session gets its own catalog-driven ``StrategyNumbers``
        / ``StrategyOptions`` (point-denominated values differ per instrument).
        """
        cfg = self.config
        registry = InstrumentRegistry(SettingsRepository(db=db), HardcodedInstrumentCatalog())

        def session_params(symbol: str) -> tuple[StrategyNumbers, StrategyOptions]:
            numbers = get_prod_strategy_numbers(
                rr_ratio=cfg.rr_ratio,
                risk_per_trade=cfg.risk_per_trade,
                risk_pct_per_trade=cfg.risk_pct_per_trade,
                account_configs=cfg.nt_accounts,
                symbol=symbol,
            )
            options = get_prod_strategy_options(
                max_bounce=numbers.max_bounce,
                min_cross_depth=numbers.min_cross_depth,
                skip_rollover_days=cfg.skip_rollover_days,
                reentry_only=cfg.reentry_only,
                line_removal_mode=DEFAULT_STRATEGY_OPTIONS.line_removal_mode,
                max_reentry_attempts=DEFAULT_STRATEGY_OPTIONS.max_reentry_attempts,
                symbol=symbol,
            )
            if cfg.no_breakeven:
                options.breakeven = None
            if cfg.no_reentry_breakeven:
                options.reentry_breakeven = None
            return numbers, options

        return registry, session_params

    # ------------------------------------------------------------------
    # Backtest
    # ------------------------------------------------------------------
    def _build_backtest(self) -> AppWiring:
        from app_factory import Repositories, create_app

        cfg = self.config
        db = _build_database(cfg)
        registry, session_params = self._build_instrument_stack(db)
        repos = Repositories(
            lines=SQLLineRepository(db=db),
            trades=SQLTradeRepository(db=db),
            trigger_state=SQLiteLineTriggerStateRepository(db=db),
        )
        accounts_repo = NtAccountRepository(db=db)

        initial_start = _parse_input_to_epoch(cfg.start_str, cfg.input_tz)
        initial_end = _parse_input_to_epoch(cfg.end_str, cfg.input_tz)

        print(f"{log_timestamp()} [App] Time Config ({cfg.pair}):")
        print(f"{log_timestamp()}       Input TZ (User): {cfg.input_tz}")
        print(f"{log_timestamp()}       File  TZ (CSV):  {cfg.file_tz}")
        print(f"{log_timestamp()}       Input Start:     {cfg.start_str} -> Epoch: {initial_start}")
        print(f"{log_timestamp()}       Input End:       {cfg.end_str}   -> Epoch: {initial_end}")

        ds = CSVDataSource(
            pair=cfg.pair,
            filename=cfg.csv_file,
            initial_start_time=initial_start,
            initial_end_time=initial_end,
            bars_per_second=cfg.bars_per_second,
            tz=cfg.file_tz,
        )

        numbers, options = session_params(cfg.pair)
        candle_config = get_prod_candle_config()

        return create_app(
            pair=cfg.pair,
            data_source=ds,
            repos=repos,
            numbers=numbers,
            options=options,
            candle_config=candle_config,
            timeframes=cfg.timeframes,
            strategy_name=cfg.strategy_name,
            bootstrap_existing_lines=cfg.bootstrap_existing_lines,
            notifier=_build_notifier(cfg),
            analytics=_build_analytics(cfg),
            app_config=cfg,
            db=db,
            accounts_repo=accounts_repo,
            instrument_registry=registry,
            session_params_provider=session_params,
            session_end_time=cfg.session_end,
        )

    # ------------------------------------------------------------------
    # Live
    # ------------------------------------------------------------------
    def _build_live(self) -> tuple[AppWiring, CombinedDataSource]:
        from app_factory import Repositories, create_app

        cfg = self.config
        db = _build_database(cfg)
        registry, session_params = self._build_instrument_stack(db)
        repos = Repositories(
            lines=SQLLineRepository(db=db),
            trades=SQLTradeRepository(db=db),
            trigger_state=SQLiteLineTriggerStateRepository(db=db),
        )
        accounts_repo = NtAccountRepository(db=db)

        notifier = _build_notifier(cfg)
        analytics = _build_analytics(cfg)
        logger = FileAndConsoleLogger(log_dir=cfg.log_dir, instance_name=cfg.instance_name)

        if cfg.risk_per_trade is not None:
            print(f"{log_timestamp()} [liquid] Risk config: fixed ${cfg.risk_per_trade:.0f} per trade")
        elif cfg.risk_pct_per_trade is not None:
            print(f"{log_timestamp()} [liquid] Risk config: {cfg.risk_pct_per_trade}% of account balance")
        else:
            print(f"{log_timestamp()} [liquid] Risk config: none (NinjaTrader will use 1 contract)")

        print(f"{log_timestamp()} [liquid] Starting ZeroMQ gateway...")
        if cfg.nt_accounts:
            live_account_configs = [a for a in cfg.nt_accounts if getattr(a, "live_enabled", True)]
            ds, executor = create_multi_account_live_components(
                cfg.pair,
                logger,
                account_configs=live_account_configs,
                risk_usd=cfg.risk_per_trade,
                risk_pct=cfg.risk_pct_per_trade,
                host=cfg.zmq_host,
                market_port=cfg.zmq_market_port,
                command_port=cfg.zmq_command_port,
                query_port=cfg.zmq_query_port,
                heartbeat_port=cfg.zmq_heartbeat_port,
                accounts_repo=accounts_repo,
                history_hours=cfg.history_hours,
                notifier=notifier,
                instrument=cfg.instrument,
                instrument_registry=registry,
            )
        else:
            ds, executor = create_live_components(
                cfg.pair,
                logger,
                risk_usd=cfg.risk_per_trade,
                risk_pct=cfg.risk_pct_per_trade,
                account_names=[],
                host=cfg.zmq_host,
                market_port=cfg.zmq_market_port,
                command_port=cfg.zmq_command_port,
                query_port=cfg.zmq_query_port,
                heartbeat_port=cfg.zmq_heartbeat_port,
                history_hours=cfg.history_hours,
                notifier=notifier,
                instrument=cfg.instrument,
                instrument_registry=registry,
            )

        numbers, options = session_params(cfg.pair)
        candle_config = get_prod_candle_config()

        wiring = create_app(
            pair=cfg.pair,
            data_source=ds,
            repos=repos,
            numbers=numbers,
            options=options,
            candle_config=candle_config,
            timeframes=cfg.timeframes,
            strategy_name=cfg.strategy_name,
            bootstrap_existing_lines=cfg.bootstrap_existing_lines,
            live_mode=True,
            trade_executor=executor,
            notifier=notifier,
            analytics=analytics,
            logger=logger,
            app_config=cfg,
            db=db,
            accounts_repo=accounts_repo,
            instrument_registry=registry,
            session_params_provider=session_params,
            session_end_time=cfg.session_end,
        )
        return wiring, ds
