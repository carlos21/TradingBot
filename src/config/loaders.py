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
from typing import Protocol, List

from src.config.models import AppConfig


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


def _csv_to_floats(val: str | None) -> List[float] | None:
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
        "RR_RATIO": ("rr_ratio", float),
        "RISK": ("risk_per_trade", _float_or_none),
        "RISK_PCT": ("risk_pct_per_trade", _float_or_none),
        "ACCOUNT_BALANCE": ("account_balance", float),
        "POINT_VALUE": ("point_value", float),
        "SL_LEVELS": ("sl_levels", _csv_to_floats),
        "MIN_STOP_LOSS": ("min_stop_loss", float),
        "MAX_BOUNCE": ("max_bounce", float),
        "EXTRA_SL_SPACE": ("extra_sl_space", float),
        "SL_LEVEL_TOLERANCE": ("sl_level_tolerance", float),
        "MIN_CROSS_DEPTH": ("min_cross_depth", float),
        "TIMEFRAMES": ("timeframes", lambda v: [x.strip() for x in v.split(",")] if v else None),
        "LINE_REMOVAL_MODE": ("line_removal_mode", str),
        "SESSION_START": ("session_start", str),
        "SESSION_END": ("session_end", str),
        "DAILY_TRADES_LIMIT": ("daily_trades_limit", int),
        "MAX_OPEN_TRADES": ("max_open_trades", int),
        "REENTRY_AFTER_SL": ("reentry_after_sl", lambda v: _bool_env("REENTRY_AFTER_SL", True)),
        "REENTRY_THRESHOLD": ("reentry_threshold", float),
        "REENTRY_ONLY": ("reentry_only", lambda v: _bool_env("REENTRY_ONLY", False)),
        "SKIP_ROLLOVER_DAYS": ("skip_rollover_days", lambda v: _bool_env("SKIP_ROLLOVER_DAYS", False)),
        "NO_BREAKEVEN": ("no_breakeven", lambda v: _bool_env("NO_BREAKEVEN", False)),
        "NO_REENTRY_BREAKEVEN": ("no_reentry_breakeven", lambda v: _bool_env("NO_REENTRY_BREAKEVEN", False)),
        "NT_ACCOUNT": ("nt_account", lambda v: v or None),
        "ZMQ_HOST": ("zmq_host", str),
        "ZMQ_MARKET_PORT": ("zmq_market_port", int),
        "ZMQ_COMMAND_PORT": ("zmq_command_port", int),
        "ZMQ_QUERY_PORT": ("zmq_query_port", int),
        "ZMQ_HEARTBEAT_PORT": ("zmq_heartbeat_port", int),
        "DB_PATH": ("db_path", str),
        "LOG_DIR": ("log_dir", str),
        "FLASK_PORT": ("flask_port", int),
        "INSTANCE_NAME": ("instance_name", str),
        "BROKER_MODE": ("broker_mode", str),
        "BROKER_SPREAD": ("broker_spread", float),
        "BOOTSTRAP_EXISTING_LINES": ("bootstrap_existing_lines", lambda v: _bool_env("BOOTSTRAP_EXISTING_LINES", True)),
        "SENTRY_DSN": ("sentry_dsn", lambda v: v or None),
        "TELEGRAM_BOT_TOKEN": ("telegram_token", lambda v: v or None),
        "TELEGRAM_CHAT_ID": ("telegram_chat_id", lambda v: v or None),
    }

    def load(self) -> AppConfig:
        cfg = AppConfig()
        for env_key, (attr, parser) in self._MAPPING.items():
            raw = os.environ.get(env_key)
            if raw is not None and raw != "":
                parsed = parser(raw)
                if parsed is not None:
                    setattr(cfg, attr, parsed)
        return cfg


# ---------------------------------------------------------------------------
# CLI loader
# ---------------------------------------------------------------------------
class CliConfigLoader:
    """Build ``AppConfig`` from ``argparse``."""

    def __init__(self, args: List[str] | None = None):
        self._args = args if args is not None else sys.argv[1:]

    def load(self) -> AppConfig:
        p = argparse.ArgumentParser(
            description="TradingBot — Live & Backtest Mode",
        )
        p.add_argument("--mode", choices=["live", "backtest"], help="Run mode")
        p.add_argument("--pair", help="Trading pair (e.g. NQ, MNQ)")
        p.add_argument("--csv-file", dest="csv_file", help="CSV file for backtest")
        p.add_argument("--bars-per-second", dest="bars_per_second", type=float, help="Replay speed")
        p.add_argument("--input-tz", dest="input_tz", help="Timezone for input dates")
        p.add_argument("--file-tz", dest="file_tz", help="Timezone for CSV timestamps")
        p.add_argument("--start", dest="start_str", help="Backtest start (YYYY-MM-DD HH:MM:SS)")
        p.add_argument("--end", dest="end_str", help="Backtest end (YYYY-MM-DD HH:MM:SS)")
        p.add_argument("--rr", dest="rr_ratio", type=float, help="Risk:Reward ratio")
        p.add_argument("--risk", dest="risk_per_trade", type=float, help="Fixed $ risk per trade")
        p.add_argument("--risk-pct", dest="risk_pct_per_trade", type=float, help="Risk %% of account")
        p.add_argument("--account-balance", dest="account_balance", type=float, help="Account balance")
        p.add_argument("--point-value", dest="point_value", type=float, help="$ per point")
        p.add_argument("--sl-levels", dest="sl_levels", help="Comma-separated SL levels")
        p.add_argument("--min-stop-loss", dest="min_stop_loss", type=float, help="Min SL points")
        p.add_argument("--max-bounce", dest="max_bounce", type=float, help="Max bounce points")
        p.add_argument("--extra-sl-space", dest="extra_sl_space", type=float, help="Extra SL space")
        p.add_argument("--sl-level-tolerance", dest="sl_level_tolerance", type=float, help="SL level tolerance")
        p.add_argument("--min-cross-depth", dest="min_cross_depth", type=float, help="Min cross depth")
        p.add_argument("--timeframes", help="Comma-separated timeframes")
        p.add_argument("--line-removal-mode", dest="line_removal_mode", choices=["ON_EVALUATE", "NEVER"], help="Line removal mode")
        p.add_argument("--session-start", dest="session_start", help="Session start HH:MM")
        p.add_argument("--session-end", dest="session_end", help="Session end HH:MM")
        p.add_argument("--daily-trades-limit", dest="daily_trades_limit", type=int, help="Max trades per day")
        p.add_argument("--max-open-trades", dest="max_open_trades", type=int, help="Max open trades")
        p.add_argument("--reentry-after-sl", dest="reentry_after_sl", type=lambda x: x.lower() in ("true", "1", "yes"), help="Re-entry after SL")
        p.add_argument("--reentry-threshold", dest="reentry_threshold", type=float, help="Re-entry threshold")
        p.add_argument("--reentry-only", dest="reentry_only", action="store_true", help="Only re-entry trades")
        p.add_argument("--skip-rollover-days", dest="skip_rollover_days", action="store_true", help="Skip rollover days")
        p.add_argument("--no-breakeven", dest="no_breakeven", action="store_true", help="Disable breakeven")
        p.add_argument("--no-reentry-breakeven", dest="no_reentry_breakeven", action="store_true", help="Disable reentry BE")
        p.add_argument("--nt-account", dest="nt_account", help="NinjaTrader account")
        p.add_argument("--zmq-host", dest="zmq_host", help="ZMQ host")
        p.add_argument("--zmq-market-port", dest="zmq_market_port", type=int, help="ZMQ market port")
        p.add_argument("--zmq-command-port", dest="zmq_command_port", type=int, help="ZMQ command port")
        p.add_argument("--zmq-query-port", dest="zmq_query_port", type=int, help="ZMQ query port")
        p.add_argument("--zmq-heartbeat-port", dest="zmq_heartbeat_port", type=int, help="ZMQ heartbeat port")
        p.add_argument("--db-path", dest="db_path", help="SQLite database path (e.g. sqlite:///./ninja.db)")
        p.add_argument("--log-dir", dest="log_dir", help="Log directory (e.g. logs/ninja)")
        p.add_argument("--flask-port", dest="flask_port", type=int, help="Flask server port")
        p.add_argument("--instance-name", dest="instance_name", help="Instance identifier for logs")
        p.add_argument("--broker-mode", dest="broker_mode", choices=["futures", "cfd"], help="Broker mode")
        p.add_argument("--broker-spread", dest="broker_spread", type=float, help="Broker spread")
        p.add_argument("--no-bootstrap-lines", dest="bootstrap_existing_lines", action="store_false", help="Skip bootstrapping lines")
        p.add_argument("--sentry-dsn", dest="sentry_dsn", help="Sentry DSN")
        p.add_argument("--telegram-token", dest="telegram_token", help="Telegram bot token")
        p.add_argument("--telegram-chat-id", dest="telegram_chat_id", help="Telegram chat ID")

        ns = p.parse_args(self._args)

        cfg = AppConfig()
        for attr in vars(cfg):
            val = getattr(ns, attr, None)
            if val is not None:
                # argparse lists come as strings when using nargs, but here we use simple types
                if attr == "sl_levels" and isinstance(val, str):
                    val = [float(x.strip()) for x in val.split(",")]
                if attr == "timeframes" and isinstance(val, str):
                    val = [x.strip() for x in val.split(",")]
                setattr(cfg, attr, val)
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
            for attr in vars(base):
                override_val = getattr(override, attr)
                default_val = getattr(AppConfig(), attr)
                # Only override if the value differs from the hardcoded default.
                # This prevents an *empty* CLI from wiping out env vars.
                if override_val != default_val:
                    setattr(base, attr, override_val)
        return base
