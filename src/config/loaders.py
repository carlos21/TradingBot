"""Configuration loaders following the Strategy pattern.

Each loader knows how to extract configuration from a specific source
(env vars, CLI args, etc.). They all produce the same ``AppConfig``
object so the rest of the application is source-agnostic.

Usage::

    loader = CompositeConfigLoader(
        EnvConfigLoader(),
        CliConfigLoader(),
    )
    config = loader.load()   # CLI overrides env vars
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Protocol

from src.config.models import AccountConfig, AppConfig


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
class ConfigLoader(Protocol):
    """Abstract configuration source."""

    def load(self) -> AppConfig:
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bool_env(key: str, default: bool) -> bool:
    """Parse a boolean env var (true/1/yes vs anything else)."""
    val = os.environ.get(key, "").lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off", ""):
        return False
    return default


def _float_or_none(val: str | None) -> float | None:
    return float(val) if val else None


def _int_or_none(val: str | None) -> int | None:
    return int(val) if val else None


def _csv_to_floats(val: str | None) -> list[float] | None:
    if not val:
        return None
    return [float(x.strip()) for x in val.split(",")]


# ---------------------------------------------------------------------------
# Env var loader
# ---------------------------------------------------------------------------
class EnvConfigLoader:
    """Build ``AppConfig`` from environment variables."""

    # Mapping: env_var_name -> (appconfig_attr, parser_func)
    _MAPPING = {
        "MODE": ("mode", str),
        "PAIR": ("pair", str),
        "CSV_FILE": ("csv_file", str),
        "BARS_PER_SECOND": ("bars_per_second", float),
        "INPUT_TZ": ("input_tz", str),
        "FILE_TZ": ("file_tz", str),
        "START_STR": ("start_str", str),
        "END_STR": ("end_str", str),
        # Account/risk config removed from env — use Settings page only
        "ACCOUNT_BALANCE": ("account_balance", float),
        "POINT_VALUE": ("point_value", float),
        "MIN_STOP_LOSS": ("min_stop_loss", float),
        "MAX_BOUNCE": ("max_bounce", float),
        "EXTRA_SL_SPACE": ("extra_sl_space", float),
        "SL_LEVEL_TOLERANCE": ("sl_level_tolerance", float),
        "MIN_CROSS_DEPTH": ("min_cross_depth", float),
        "TIMEFRAMES": ("timeframes", lambda _v: [x.strip() for x in _v.split(",")] if _v else None),
        "LINE_REMOVAL_MODE": ("line_removal_mode", str),
        "SESSION_START": ("session_start", str),
        "SESSION_END": ("session_end", str),
        "DAILY_TRADES_LIMIT": ("daily_trades_limit", int),
        "MAX_OPEN_TRADES": ("max_open_trades", int),
        "REENTRY_AFTER_SL": ("reentry_after_sl", lambda _v: _bool_env("REENTRY_AFTER_SL", True)),
        "REENTRY_THRESHOLD": ("reentry_threshold", float),
        "REENTRY_ONLY": ("reentry_only", lambda _v: _bool_env("REENTRY_ONLY", False)),
        "SKIP_ROLLOVER_DAYS": ("skip_rollover_days", lambda _v: _bool_env("SKIP_ROLLOVER_DAYS", False)),
        "NO_BREAKEVEN": ("no_breakeven", lambda _v: _bool_env("NO_BREAKEVEN", False)),
        "NO_REENTRY_BREAKEVEN": ("no_reentry_breakeven", lambda _v: _bool_env("NO_REENTRY_BREAKEVEN", False)),
        # NT_ACCOUNTS removed from env — use Settings page only
        "ZMQ_HOST": ("zmq_host", str),
        "ZMQ_MARKET_PORT": ("zmq_market_port", int),
        "ZMQ_COMMAND_PORT": ("zmq_command_port", int),
        "ZMQ_QUERY_PORT": ("zmq_query_port", int),
        "ZMQ_HEARTBEAT_PORT": ("zmq_heartbeat_port", int),
        "DB_PATH": ("db_path", str),
        "LOG_DIR": ("log_dir", str),
        "FLASK_PORT": ("flask_port", int),
        "INSTANCE_NAME": ("instance_name", str),
        "PLATFORM_TYPE": ("platform_type", str),
        "BROKER_MODE": ("broker_mode", str),
        "BROKER_SPREAD": ("broker_spread", float),
        "BOOTSTRAP_EXISTING_LINES": ("bootstrap_existing_lines", lambda _v: _bool_env("BOOTSTRAP_EXISTING_LINES", True)),
        "SENTRY_DSN": ("sentry_dsn", lambda _v: _v or None),
        "TELEGRAM_BOT_TOKEN": ("telegram_token", lambda _v: _v or None),
        "TELEGRAM_CHAT_ID": ("telegram_chat_id", lambda _v: _v or None),
    }

    def load(self) -> AppConfig:
        cfg = AppConfig()
        for env_key, (attr, parser) in self._MAPPING.items():
            raw = os.environ.get(env_key)
            if raw is not None and raw != "":
                parsed = parser(raw)
                if parsed is not None:
                    setattr(cfg, attr, parsed)
        cfg.validate()
        return cfg


# ---------------------------------------------------------------------------
# CLI loader
# ---------------------------------------------------------------------------
class CliConfigLoader:
    """Build ``AppConfig`` from ``argparse``."""

    def __init__(self, args: list[str] | None = None):
        self._args = args if args is not None else sys.argv[1:]

    def load(self) -> AppConfig:
        p = argparse.ArgumentParser(
            description="Liquid — Live & Backtest Mode",
        )

        # Wrapper that prevents argparse from polluting the namespace with default
        # values.  Only arguments actually supplied on the command line are
        # emitted, so CompositeConfigLoader can distinguish "not provided" from
        # "provided but equal to the default".
        def _add(*args, **kwargs):
            kwargs.setdefault("default", argparse.SUPPRESS)
            return p.add_argument(*args, **kwargs)

        _add("--mode", choices=["live", "backtest"], help="Run mode")
        _add("--strategy-name", dest="strategy_name", choices=["liquidity_v2", "tsi_cross"], help="Strategy to run")
        _add("--pair", help="Trading pair (e.g. MNQ)")
        _add("--csv-file", dest="csv_file", help="CSV file for backtest")
        _add("--bars-per-second", dest="bars_per_second", type=float, help="Replay speed")
        _add("--input-tz", dest="input_tz", help="Timezone for input dates")
        _add("--file-tz", dest="file_tz", help="Timezone for CSV timestamps")
        _add("--start", dest="start_str", help="Backtest start (YYYY-MM-DD HH:MM:SS)")
        _add("--end", dest="end_str", help="Backtest end (YYYY-MM-DD HH:MM:SS)")
        _add("--rr", dest="rr_ratio", type=float, help="Risk:Reward ratio")
        _add("--risk", dest="risk_per_trade", type=float, help="Fixed $ risk per trade")
        _add("--risk-pct", dest="risk_pct_per_trade", type=float, help="Risk %% of account")
        _add("--account-balance", dest="account_balance", type=float, help="Account balance")
        _add("--point-value", dest="point_value", type=float, help="$ per point")
        _add("--min-stop-loss", dest="min_stop_loss", type=float, help="Min SL points")
        _add("--max-bounce", dest="max_bounce", type=float, help="Max bounce points")
        _add("--extra-sl-space", dest="extra_sl_space", type=float, help="Extra SL space")
        _add("--sl-level-tolerance", dest="sl_level_tolerance", type=float, help="SL level tolerance")
        _add("--min-cross-depth", dest="min_cross_depth", type=float, help="Min cross depth")
        _add("--timeframes", help="Comma-separated timeframes")
        _add("--line-removal-mode", dest="line_removal_mode", choices=["ON_EVALUATE", "NEVER"], help="Line removal mode")
        _add("--session-start", dest="session_start", help="Session start HH:MM")
        _add("--session-end", dest="session_end", help="Session end HH:MM")
        _add("--daily-trades-limit", dest="daily_trades_limit", type=int, help="Max trades per day")
        _add("--max-open-trades", dest="max_open_trades", type=int, help="Max open trades")
        _add("--reentry-after-sl", dest="reentry_after_sl", type=lambda _x: _x.lower() in ("true", "1", "yes"), help="Re-entry after SL")
        _add("--reentry-threshold", dest="reentry_threshold", type=float, help="Re-entry threshold")
        _add("--reentry-only", dest="reentry_only", action="store_true", help="Only re-entry trades")
        _add("--skip-rollover-days", dest="skip_rollover_days", action="store_true", help="Skip rollover days")
        _add("--no-breakeven", dest="no_breakeven", action="store_true", help="Disable breakeven")
        _add("--no-reentry-breakeven", dest="no_reentry_breakeven", action="store_true", help="Disable reentry BE")
        # NT accounts are managed via the Settings page / DB only
        _add("--zmq-host", dest="zmq_host", help="ZMQ host")
        _add("--zmq-market-port", dest="zmq_market_port", type=int, help="ZMQ market port")
        _add("--zmq-command-port", dest="zmq_command_port", type=int, help="ZMQ command port")
        _add("--zmq-query-port", dest="zmq_query_port", type=int, help="ZMQ query port")
        _add("--zmq-heartbeat-port", dest="zmq_heartbeat_port", type=int, help="ZMQ heartbeat port")
        _add("--db-path", dest="db_path", help="SQLite database path (e.g. sqlite:///./ninja.db)")
        _add("--log-dir", dest="log_dir", help="Log directory (e.g. logs/ninja)")
        _add("--flask-port", dest="flask_port", type=int, help="Flask server port")
        _add("--instance-name", dest="instance_name", help="Instance identifier for logs")
        _add("--platform-type", dest="platform_type", choices=["ninjatrader", "metatrader"], help="Trading platform this instance connects to")
        _add("--broker-mode", dest="broker_mode", choices=["futures", "cfd"], help="Broker mode")
        _add("--broker-spread", dest="broker_spread", type=float, help="Broker spread")
        _add("--no-bootstrap-lines", dest="bootstrap_existing_lines", action="store_false", help="Skip bootstrapping lines")
        _add("--sentry-dsn", dest="sentry_dsn", help="Sentry DSN")
        _add("--telegram-token", dest="telegram_token", help="Telegram bot token")
        _add("--telegram-chat-id", dest="telegram_chat_id", help="Telegram chat ID")

        ns = p.parse_args(self._args)
        provided = set(vars(ns).keys())

        cfg = AppConfig()
        for attr, val in vars(ns).items():
            if attr == "timeframes" and isinstance(val, str):
                val = [x.strip() for x in val.split(",")]
            setattr(cfg, attr, val)
        # CompositeConfigLoader needs to know which args were actually supplied
        # so that an explicit "--foo default" can reset an earlier non-default.
        cfg._cli_provided = provided  # type: ignore[attr-defined]
        cfg.validate()
        return cfg


# ---------------------------------------------------------------------------
# Composite loader (merges multiple sources, later overrides earlier)
# ---------------------------------------------------------------------------
class CompositeConfigLoader:
    """Merge multiple loaders. Later loaders override earlier ones field-by-field."""

    def __init__(self, *loaders: ConfigLoader):
        self.loaders = loaders

    def load(self) -> AppConfig:
        if not self.loaders:
            return AppConfig()

        base = self.loaders[0].load()
        for loader in self.loaders[1:]:
            override = loader.load()
            cli_provided = getattr(override, "_cli_provided", None)
            if cli_provided is not None:
                # CLI: only override the arguments the user actually typed,
                # even if they equal the hard-coded default.
                attrs = cli_provided
            else:
                # Env / DB: skip attributes that still have the default value,
                # because those were not explicitly configured by the source.
                attrs = vars(override)
            for attr in attrs:
                override_val = getattr(override, attr)
                if cli_provided is None:
                    default_val = getattr(AppConfig(), attr)
                    if override_val == default_val:
                        continue
                setattr(base, attr, override_val)
        base.validate()
        return base


# ---------------------------------------------------------------------------
# Database loader (reads runtime settings from SQLite)
# ---------------------------------------------------------------------------
class DbConfigLoader:
    """Override AppConfig with values stored in the SQLite database."""

    def __init__(self, db_path: str = "sqlite:///./database.db"):
        self._db_path = db_path

    def load(self) -> AppConfig:
        cfg = AppConfig()
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            engine = create_engine(self._db_path)
            Session = sessionmaker(bind=engine)
            session = Session()

            from src.infrastructure.database.database import AppSetting, NtAccount
            from src.infrastructure.database.database_protocol import Base

            Base.metadata.create_all(engine)

            settings = session.query(AppSetting).all()
            for row in settings:
                if row.value is None or row.value == "":
                    continue
                attr = self._key_to_attr(row.key)
                if attr and hasattr(cfg, attr):
                    parsed = self._parse_attr(attr, row.value)
                    if parsed is not None:
                        setattr(cfg, attr, parsed)

            accounts = session.query(NtAccount).all()
            if accounts:
                cfg.nt_accounts = [
                    AccountConfig(name=a.name, risk_usd=a.risk_usd, risk_pct=a.risk_pct, rr_ratio=a.rr_ratio)
                    for a in accounts
                ]
                # Derive global defaults from first account for backtest/strategy compatibility
                first = cfg.nt_accounts[0]
                if first.rr_ratio is not None:
                    cfg.rr_ratio = first.rr_ratio
                if first.risk_usd is not None:
                    cfg.risk_per_trade = first.risk_usd
                if first.risk_pct is not None:
                    cfg.risk_pct_per_trade = first.risk_pct

            cfg.validate()
            session.close()
        except Exception as e:
            # If DB is unreachable or tables missing, log and fall back to env/CLI
            import logging
            logging.getLogger(__name__).warning(f"DB config load failed: {e}. Falling back to env/CLI defaults.")
        return cfg

    @staticmethod
    def _key_to_attr(key: str) -> str | None:
        mapping = {
            "pair": "pair",
            "instrument": "instrument",
            "risk_per_trade": "risk_per_trade",
            "risk_pct_per_trade": "risk_pct_per_trade",
            "rr_ratio": "rr_ratio",
            "flask_port": "flask_port",
            "zmq_host": "zmq_host",
            "zmq_market_port": "zmq_market_port",
            "zmq_command_port": "zmq_command_port",
            "zmq_query_port": "zmq_query_port",
            "zmq_heartbeat_port": "zmq_heartbeat_port",
            "account_balance": "account_balance",
            "point_value": "point_value",
            "min_stop_loss": "min_stop_loss",
            "max_bounce": "max_bounce",
            "extra_sl_space": "extra_sl_space",
            "sl_level_tolerance": "sl_level_tolerance",
            "min_cross_depth": "min_cross_depth",
            "line_removal_mode": "line_removal_mode",
            "session_start": "session_start",
            "session_end": "session_end",
            "daily_trades_limit": "daily_trades_limit",
            "max_open_trades": "max_open_trades",
            "reentry_threshold": "reentry_threshold",
            "broker_mode": "broker_mode",
            "broker_spread": "broker_spread",
            "history_days": "history_days",
        }
        return mapping.get(key)

    @staticmethod
    def _parse_attr(attr: str, value: str):
        if attr in ("risk_per_trade", "risk_pct_per_trade", "rr_ratio", "account_balance",
                    "point_value", "min_stop_loss", "max_bounce", "extra_sl_space",
                    "sl_level_tolerance", "min_cross_depth", "reentry_threshold", "broker_spread"):
            return float(value)
        if attr in ("flask_port", "zmq_market_port", "zmq_command_port", "zmq_query_port",
                    "zmq_heartbeat_port", "daily_trades_limit", "max_open_trades", "history_days"):
            return int(value)
        return value
