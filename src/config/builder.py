"""Application builder — turns ``AppConfig`` into a fully wired ``AppWiring``.

This module replaces the monolithic ``build_prod()`` / ``build_live()``
functions that previously lived in ``app.py``. It follows SRP by
containing *only* the "how do I assemble the app?" logic.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app_factory import AppWiring, Repositories, create_app
from src.analytics import AnalyticsReporter, NoOpReporter, SentryReporter
from src.config.models import AppConfig
from src.data_sources.combined_datasource import CombinedDataSource
from src.data_sources.csv_datasource import CSVDataSource
from src.database import database
from src.notifier import NoOpNotifier, Notifier, TelegramNotifier
from src.prod_config import (
    get_prod_candle_config,
    get_prod_strategy_numbers,
    get_prod_strategy_options,
)
from src.repositories.line_trigger_state_repository import (
    SQLiteLineTriggerStateRepository,
)
from src.repositories.lines_repository import SQLLineRepository
from src.repositories.trades_repository import SQLTradeRepository
from src.utils.app_logger import FileAndConsoleLogger


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
        print("[App] Sentry analytics enabled")
        return SentryReporter(config.sentry_dsn)
    return NoOpReporter()


def _build_notifier(config: AppConfig) -> Notifier:
    if config.telegram_token and config.telegram_chat_id:
        print("[App] Telegram notifications enabled")
        return TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    return NoOpNotifier()


class AppBuilder:
    """Assemble the application from a configuration object."""

    def __init__(self, config: AppConfig):
        self.config = config

    def build(self) -> tuple[AppWiring, CombinedDataSource | None]:
        if self.config.mode == "live":
            return self._build_live()
        return self._build_backtest(), None

    # ------------------------------------------------------------------
    # Backtest
    # ------------------------------------------------------------------
    def _build_backtest(self) -> AppWiring:
        cfg = self.config
        database.setup_database(db_url=cfg.db_path)
        repos = Repositories(
            lines=SQLLineRepository(),
            trades=SQLTradeRepository(),
            trigger_state=SQLiteLineTriggerStateRepository(),
        )

        initial_start = _parse_input_to_epoch(cfg.start_str, cfg.input_tz)
        initial_end = _parse_input_to_epoch(cfg.end_str, cfg.input_tz)

        print(f"[App] Time Config ({cfg.pair}):")
        print(f"      Input TZ (User): {cfg.input_tz}")
        print(f"      File  TZ (CSV):  {cfg.file_tz}")
        print(f"      Input Start:     {cfg.start_str} -> Epoch: {initial_start}")
        print(f"      Input End:       {cfg.end_str}   -> Epoch: {initial_end}")

        ds = CSVDataSource(
            pair=cfg.pair,
            filename=cfg.csv_file,
            initial_start_time=initial_start,
            initial_end_time=initial_end,
            bars_per_second=cfg.bars_per_second,
            tz=cfg.file_tz,
        )

        numbers = get_prod_strategy_numbers(
            rr_ratio=cfg.rr_ratio,
            risk_per_trade=cfg.risk_per_trade,
            risk_pct_per_trade=cfg.risk_pct_per_trade,
        )
        candle_config = get_prod_candle_config()
        options = get_prod_strategy_options(
            max_bounce=numbers.max_bounce,
            min_cross_depth=numbers.min_cross_depth,
            skip_rollover_days=cfg.skip_rollover_days,
            reentry_only=cfg.reentry_only,
        )
        if cfg.no_breakeven:
            options.breakeven = None
        if cfg.no_reentry_breakeven:
            options.reentry_breakeven = None

        return create_app(
            pair=cfg.pair,
            data_source=ds,
            repos=repos,
            numbers=numbers,
            options=options,
            candle_config=candle_config,
            timeframes=cfg.timeframes,
            bootstrap_existing_lines=cfg.bootstrap_existing_lines,
            notifier=_build_notifier(cfg),
            analytics=_build_analytics(cfg),
        ), None

    # ------------------------------------------------------------------
    # Live
    # ------------------------------------------------------------------
    def _build_live(self) -> tuple[AppWiring, CombinedDataSource]:
        from src.gateway import create_live_components

        cfg = self.config
        database.setup_database(db_url=cfg.db_path)
        repos = Repositories(
            lines=SQLLineRepository(),
            trades=SQLTradeRepository(),
            trigger_state=SQLiteLineTriggerStateRepository(),
        )

        notifier = _build_notifier(cfg)
        analytics = _build_analytics(cfg)
        logger = FileAndConsoleLogger(log_dir=cfg.log_dir, instance_name=cfg.instance_name)

        if cfg.risk_per_trade is not None:
            print(f"[tradingbot] Risk config: fixed ${cfg.risk_per_trade:.0f} per trade")
        elif cfg.risk_pct_per_trade is not None:
            print(f"[tradingbot] Risk config: {cfg.risk_pct_per_trade}% of account balance")
        else:
            print("[tradingbot] Risk config: none (NinjaTrader will use 1 contract)")

        print("[tradingbot] Starting ZeroMQ gateway...")
        ds, executor = create_live_components(
            cfg.pair,
            logger,
            risk_usd=cfg.risk_per_trade,
            risk_pct=cfg.risk_pct_per_trade,
            account=cfg.nt_account,
            host=cfg.zmq_host,
            market_port=cfg.zmq_market_port,
            command_port=cfg.zmq_command_port,
            query_port=cfg.zmq_query_port,
            heartbeat_port=cfg.zmq_heartbeat_port,
        )

        numbers = get_prod_strategy_numbers(
            rr_ratio=cfg.rr_ratio,
            risk_per_trade=cfg.risk_per_trade,
            risk_pct_per_trade=cfg.risk_pct_per_trade,
        )
        candle_config = get_prod_candle_config()
        options = get_prod_strategy_options(
            max_bounce=numbers.max_bounce,
            min_cross_depth=numbers.min_cross_depth,
            skip_rollover_days=cfg.skip_rollover_days,
            reentry_only=cfg.reentry_only,
        )
        if cfg.no_breakeven:
            options.breakeven = None
        if cfg.no_reentry_breakeven:
            options.reentry_breakeven = None

        wiring = create_app(
            pair=cfg.pair,
            data_source=ds,
            repos=repos,
            numbers=numbers,
            options=options,
            candle_config=candle_config,
            timeframes=cfg.timeframes,
            bootstrap_existing_lines=cfg.bootstrap_existing_lines,
            live_mode=True,
            trade_executor=executor,
            notifier=notifier,
            analytics=analytics,
            logger=logger,
        )
        return wiring, ds
