"""Central configuration models for the trading bot.

All tunable application parameters are defined here as a single dataclass.
This follows SRP (one place for config shape) and makes the app
configurable from any source (env vars, CLI args, YAML, etc.) without
touching business logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_HISTORY_HOURS = 720  # 30 days


@dataclass
class AccountConfig:
    """Configuration for a single NinjaTrader account."""
    name: str
    risk_usd: float | None = None
    risk_pct: float | None = None
    rr_ratio: float | None = None
    live_enabled: bool = True
    instrument_symbols: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    """Complete application configuration.

    Every field maps to either an environment variable or a CLI flag.
    CLI flags take precedence over env vars when both are provided.
    """

    # ------------------------------------------------------------------
    # Mode & Pair
    # ------------------------------------------------------------------
    mode: str = "backtest"
    """Run mode: ``"live"`` for ZeroMQ live trading, ``"backtest"`` for CSV replay."""

    pair: str = "MNQ"
    """Trading instrument (e.g. ``"MNQ"``, ``"MNQ"``, ``"ES"``).
    Passed to data sources, strategies, and repositories."""

    instrument: str | None = None
    """Full NinjaTrader instrument name (e.g. ``"MNQ 09-26"``).
    Sent to NinjaTrader in every order command. Required for live trading."""

    # ------------------------------------------------------------------
    # Strategy selection
    # ------------------------------------------------------------------
    strategy_name: str = "liquidity_v2"
    """Which strategy to run.
    ``"liquidity_v2"`` = existing LiquidityStrategyV2 (default).
    ``"tsi_cross"``    = new line-less TSI cross strategy.
    """

    # ------------------------------------------------------------------
    # Data source (backtest only)
    # ------------------------------------------------------------------
    csv_file: str = "csvs/NQ_live.csv"
    """Path to the CSV file replayed in backtest mode."""

    bars_per_second: float = 10.0
    """Backtest replay speed: how many 1-minute bars are emitted per second.
    Higher = faster backtest."""

    input_tz: str = "America/New_York"
    """Timezone used to interpret ``start_str`` / ``end_str`` (the dates you type)."""

    file_tz: str = "America/Chicago"
    """Timezone of the timestamps stored in the CSV file."""

    start_str: str = "2026-02-22 17:00:00"
    """Backtest start date (interpreted in ``input_tz``)."""

    end_str: str = "2026-10-18 06:45:00"
    """Backtest end date (interpreted in ``input_tz``)."""

    # ------------------------------------------------------------------
    # Strategy numbers
    # ------------------------------------------------------------------
    rr_ratio: float = 5.0
    """Risk:Reward ratio used to calculate the Take-Profit distance.
    E.g. ``5.0`` means TP is 5× the risk distance from entry."""

    risk_per_trade: float | None = None
    """Fixed dollar amount to risk per trade (e.g. ``50`` = $50).
    Overrides ``risk_pct_per_trade`` when set."""

    risk_pct_per_trade: float | None = None
    """Percentage of ``account_balance`` to risk per trade (e.g. ``1.0`` = 1%%).
    Only used when ``risk_per_trade`` is ``None``."""

    account_balance: float = 100000.0
    """Current account balance in USD. Used for position-sizing calculations."""

    point_value: float = 2.0
    """Dollar value of one price point for the traded instrument.
    ``2.0`` = MNQ ($2 per point). Used for backtest/analytics only;
    live trading uses the instrument's point value from the broker platform."""

    min_stop_loss: float = 10.0
    """Minimum allowed stop-loss distance in points.
    Prevents trades with unrealistically tight SL."""

    max_bounce: float = 90.0
    """Maximum allowed price bounce (points) from a strategy line before the line
    is invalidated and removed."""

    extra_sl_space: float = 0.0
    """Additional buffer added to the calculated stop-loss (points)."""

    sl_level_tolerance: float = 3.0
    """Tolerance in points when matching price-to-structural-extreme distances
    against the tiered ``sl_levels``."""

    min_cross_depth: float = 5.0
    """Minimum price penetration (points) past a strategy line required to
    count as a valid cross/entry trigger."""

    # ------------------------------------------------------------------
    # Strategy options
    # ------------------------------------------------------------------
    timeframes: list[str] = field(default_factory=lambda: ["3m", "5m", "15m", "30m", "1h"])
    """Candle timeframes used for multi-timeframe analysis and aggregation.
    Affects trigger evaluation and chart emission."""

    line_removal_mode: str = "ON_EVALUATE"
    """When to remove invalidated strategy lines:
    ``"ON_EVALUATE"`` = remove after each bar evaluation,
    ``"NEVER"`` = keep lines indefinitely."""

    session_start: str = "08:00"
    """Trading session start time (HH:MM, in ``America/New_York`` by default).
    Entry filters block signals outside this window."""

    session_end: str = "16:58"
    """Trading session end time (HH:MM). Open trades are closed at this time
    in live mode to avoid overnight exposure."""

    daily_trades_limit: int = 1
    """Maximum number of new trades allowed per trading day.
    Used by the ``daily_trades_limit_filter`` entry filter."""

    max_open_trades: int = 1
    """Maximum number of simultaneously open trades.
    Used by the ``open_trades_limit_filter`` entry filter."""

    reentry_after_sl: bool = True
    """If ``True``, allows the strategy to re-enter on the same line after a stop-loss
    is hit (if price returns)."""

    reentry_threshold: float = 90.0
    """Cancel re-entry opportunity if price moves this many points past the line
    after a stop-loss."""

    reentry_only: bool = False
    """If ``True``, skips the initial trade and only takes re-entry trades."""

    skip_rollover_days: bool = False
    """If ``True``, disables trading on futures contract rollover days.
    Used by the ``rollover_filter`` entry filter."""

    no_breakeven: bool = False
    """Runtime override: ``True`` disables the breakeven stop-loss move."""

    no_reentry_breakeven: bool = False
    """Runtime override: ``True`` disables the re-entry breakeven logic."""

    # ------------------------------------------------------------------
    # Live mode (ZeroMQ / NinjaTrader)
    # ------------------------------------------------------------------
    nt_accounts: list[AccountConfig] = field(default_factory=list)
    """List of NinjaTrader accounts to trade, each with optional per-account risk.
    Replaces the old single ``nt_account`` field."""

    zmq_host: str = "127.0.0.1"
    """ZeroMQ broker host (NinjaTrader runs on the same machine by default)."""

    zmq_market_port: int = 5555
    """ZeroMQ port for market data (ticks / bars) from NinjaTrader."""

    zmq_command_port: int = 5556
    """ZeroMQ port for sending trade commands (open / close / modify) to NinjaTrader."""

    zmq_query_port: int = 5557
    """ZeroMQ port for querying NinjaTrader (account info, positions)."""

    zmq_heartbeat_port: int = 5558
    """ZeroMQ port for heartbeat / connection health checks."""

    # ------------------------------------------------------------------
    # Multi-instance isolation
    # ------------------------------------------------------------------
    database_url: str | None = None
    """SQLAlchemy database URL. Overrides ``db_path`` when set.

    Examples:
      - ``sqlite:///./database.db`` (default fallback)
      - ``postgresql://user:pass@host/db``
      - ``mysql+pymysql://user:pass@host/db``
      - ``mariadb+pymysql://user:pass@host/db``
    """

    db_path: str = "sqlite:///./database.db"
    """SQLite database file path. Kept for backward compatibility.
    Use ``database_url`` for PostgreSQL/MySQL/MariaDB."""

    log_dir: str = "logs"
    """Directory for log files. Use a different directory per instance to
    prevent log mixing when running multiple platforms simultaneously."""

    flask_port: int = 5001
    """Flask/SocketIO server port. Must be unique per instance."""

    instance_name: str = "tradingbot"
    """Identifier for this app instance. Included in log output to help
    distinguish between multiple running instances."""

    platform_type: str = "ninjatrader"
    """Platform this instance connects to: ``"ninjatrader"`` or ``"metatrader"``.
    Drives UI labels, auto-launch behaviour, and admin tabs."""

    history_hours: int = DEFAULT_HISTORY_HOURS
    """Number of hours of historical bars to load from the platform on connect."""

    # ------------------------------------------------------------------
    # Infra
    # ------------------------------------------------------------------
    broker_mode: str = "futures"
    """Broker pricing model: ``"futures"`` (commissions per contract) or
    ``"cfd"`` (spread-based). Affects SL/TP hit detection and PnL calc."""

    broker_spread: float = 0.0
    """Spread in points for CFD mode. Deducted from PnL (as a cost) on trade
    close when ``broker_mode`` is ``"cfd"``. Does NOT affect SL/TP hit
    detection — that uses the raw price levels."""

    bootstrap_existing_lines: bool = True
    """If ``True``, loads pre-existing lines from the DB into the strategy on startup.
    Ensures lines survive app restarts."""

    # ------------------------------------------------------------------
    # Integrations
    # ------------------------------------------------------------------
    sentry_dsn: str | None = None
    """Sentry DSN for error tracking and performance monitoring.
    If ``None``, Sentry is disabled."""

    telegram_token: str | None = None
    """Telegram Bot API token for trade notifications.
    Both token and ``telegram_chat_id`` must be set to enable notifications."""

    telegram_chat_id: str | None = None
    """Telegram chat / channel ID to send notifications to."""

    # ------------------------------------------------------------------
    # Internal bookkeeping
    # ------------------------------------------------------------------
    _cli_provided: set[str] = field(default_factory=set, repr=False, compare=False)
    """Attribute names explicitly supplied on the command line. Populated by
    ``CliConfigLoader`` so ``CompositeConfigLoader`` can distinguish
    "not provided" from "provided but equal to the default"."""

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    def __post_init__(self):
        # Ensure rr_ratio is float
        self.rr_ratio = float(self.rr_ratio)
        if self.risk_per_trade is not None:
            self.risk_per_trade = float(self.risk_per_trade)
        if self.risk_pct_per_trade is not None:
            self.risk_pct_per_trade = float(self.risk_pct_per_trade)
        self.validate()

    def validate(self) -> None:
        """Validate configuration values that would silently break trading math."""
        if self.rr_ratio <= 0:
            raise ValueError("rr_ratio must be positive")
        if self.risk_per_trade is not None and self.risk_per_trade < 0:
            raise ValueError("risk_per_trade cannot be negative")
        if self.risk_pct_per_trade is not None and self.risk_pct_per_trade < 0:
            raise ValueError("risk_pct_per_trade cannot be negative")
        if self.account_balance <= 0:
            raise ValueError("account_balance must be positive")
        if self.point_value <= 0:
            raise ValueError("point_value must be positive")
        if self.min_stop_loss < 0:
            raise ValueError("min_stop_loss cannot be negative")
        if self.max_bounce < 0:
            raise ValueError("max_bounce cannot be negative")
        if self.extra_sl_space < 0:
            raise ValueError("extra_sl_space cannot be negative")
        if self.sl_level_tolerance < 0:
            raise ValueError("sl_level_tolerance cannot be negative")
        if self.min_cross_depth < 0:
            raise ValueError("min_cross_depth cannot be negative")
        if self.reentry_threshold < 0:
            raise ValueError("reentry_threshold cannot be negative")
        if self.daily_trades_limit < 0:
            raise ValueError("daily_trades_limit cannot be negative")
        if self.max_open_trades < 0:
            raise ValueError("max_open_trades cannot be negative")
        if self.broker_spread < 0:
            raise ValueError("broker_spread cannot be negative")
        if self.history_hours <= 0:
            raise ValueError("history_hours must be positive")
        if self.bars_per_second <= 0:
            raise ValueError("bars_per_second must be positive")
        if not (0 < self.flask_port <= 65535):
            raise ValueError("flask_port must be a valid TCP port (1-65535)")
        if self.mode not in ("live", "backtest"):
            raise ValueError("mode must be 'live' or 'backtest'")
        if self.broker_mode not in ("futures", "cfd"):
            raise ValueError("broker_mode must be 'futures' or 'cfd'")
        if self.platform_type not in ("ninjatrader", "metatrader"):
            raise ValueError("platform_type must be 'ninjatrader' or 'metatrader'")
        if self.line_removal_mode not in ("ON_EVALUATE", "NEVER"):
            raise ValueError("line_removal_mode must be 'ON_EVALUATE' or 'NEVER'")
        if not self.timeframes:
            raise ValueError("timeframes cannot be empty")
        for tf in self.timeframes:
            if not isinstance(tf, str) or not tf.strip():
                raise ValueError("each timeframe must be a non-empty string")
        for acct in self.nt_accounts:
            if not acct.name:
                raise ValueError("account name cannot be empty")
            if acct.risk_usd is not None and acct.risk_usd < 0:
                raise ValueError("account risk_usd cannot be negative")
            if acct.risk_pct is not None and acct.risk_pct < 0:
                raise ValueError("account risk_pct cannot be negative")
            if acct.rr_ratio is not None and acct.rr_ratio <= 0:
                raise ValueError("account rr_ratio must be positive")
            if not isinstance(acct.instrument_symbols, list):
                raise ValueError("account instrument_symbols must be a list")
            for sym in acct.instrument_symbols:
                if not isinstance(sym, str) or not sym.strip():
                    raise ValueError("each account instrument_symbol must be a non-empty string")
