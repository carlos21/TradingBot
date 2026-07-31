from contextlib import contextmanager

from sqlalchemy import JSON, Column, DateTime, Float, Index, Integer, String, Text, func

from src.infrastructure.database.database_protocol import Base, DatabaseProtocol, get_database


class Line(Base):
    __tablename__ = "lines"

    line_id = Column(String(50), primary_key=True, index=False)
    pair = Column(String(10), nullable=False)
    price = Column(Float, nullable=False)
    creation_date = Column(DateTime(timezone=True), nullable=False)

class Trade(Base):
    __tablename__ = "trades"

    trade_id     = Column(String(50), primary_key=True, index=False)
    pair         = Column(String(10), nullable=False)
    trade_type   = Column(String(10), nullable=False)   # "long" or "short"
    entry_price  = Column(Float,   nullable=False)
    stop_loss    = Column(Float,   nullable=True)
    take_profit  = Column(Float,   nullable=False)
    risk         = Column(Float,   nullable=False)
    risk_dollars = Column(Float,   nullable=True)
    risk_pct     = Column(Float,   nullable=True)
    account_balance = Column(Float, nullable=True)
    contracts    = Column(Float,   nullable=True)
    entry_time   = Column(DateTime(timezone=True), nullable=False)
    exit_price   = Column(Float,   nullable=True)
    exit_time    = Column(DateTime(timezone=True), nullable=True)
    result       = Column(Float,   nullable=True)       # e.g. PnL or +1/–1 flag
    result_type  = Column(String(10), nullable=True)    # "TP", "SL", "BE", "SP", or "CLOSE" (null while open)
    fees         = Column(Float,   nullable=True)       # commission/fees deducted from the trade
    pnl_usd      = Column(Float,   nullable=True)       # net realized PnL after fees
    original_entry_price = Column(Float, nullable=True) # strategy-calculated entry before slippage
    gross_pnl    = Column(Float,   nullable=True)       # realized PnL before fees
    realized_pnl = Column(Float,   nullable=True)       # net realized PnL after fees (mirror of pnl_usd)
    params       = Column(JSON,    nullable=True)       # any extra metadata (e.g. {"rr": "1:4"})
    logs         = Column(JSON,    nullable=True)       # per-trade lifecycle log entries
    source       = Column(String(20), nullable=True)    # strategy, manual, test
    account      = Column(String(50), nullable=True)    # NT account name (NULL for signal trades)
    signal_id    = Column(String(50), nullable=True)    # parent signal trade_id for multi-account expansion
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LineTriggerState(Base):
    __tablename__ = "line_trigger_state"

    line_id    = Column(String(50), primary_key=True)
    pair       = Column(String(10), nullable=False)
    state_json = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    bar_time     = Column(Float, nullable=False)
    pair         = Column(String(10), nullable=False)
    tf           = Column(String(10), nullable=True)
    line_id      = Column(String(50), nullable=True)
    event        = Column(String(20), nullable=False)
    direction    = Column(String(10), nullable=True)
    trigger_name = Column(String(50), nullable=True)
    filter_name  = Column(String(50), nullable=True)
    reason       = Column(String(255), nullable=True)
    details      = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_decision_logs_bar_time", "bar_time"),
        Index("ix_decision_logs_event", "event"),
        Index("ix_decision_logs_line_id", "line_id"),
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    key          = Column(String(100), nullable=False, unique=True)
    value        = Column(Text, nullable=True)
    is_sensitive = Column(Integer, default=0)  # 0/1 boolean
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class NtAccount(Base):
    __tablename__ = "nt_accounts"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    name               = Column(String(100), nullable=False, unique=True)
    risk_usd           = Column(Float, nullable=True)
    risk_pct           = Column(Float, nullable=True)
    rr_ratio           = Column(Float, nullable=True)
    live_enabled       = Column(Integer, default=1)  # 0/1 boolean; None treated as enabled
    instrument_symbols = Column(JSON, nullable=True)  # list[str]; None/empty = no instruments (strict)
    updated_at         = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AppCredential(Base):
    __tablename__ = "app_credentials"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    service            = Column(String(50), nullable=False)
    username           = Column(String(200), nullable=False)
    password_encrypted = Column(Text, nullable=False)
    updated_at         = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


db = None

@contextmanager
def get_db_session():
    if db is None:
        raise RuntimeError("Database not initialized. Call setup_database() first.")
    db_session = db.get_session()
    try:
        yield db_session
    finally:
        db_session.close()

def _column_type_sql(column, dialect) -> str:
    """Render a SQLAlchemy Column's type clause for a given dialect."""
    return str(column.type.compile(dialect=dialect))


def _add_column_if_missing(engine, table_name: str, column, defaults: dict | None = None):
    """Add ``column`` to ``table_name`` if it does not already exist.

    Works across SQLite, PostgreSQL and MySQL by rendering the column type
    through SQLAlchemy's dialect compiler and applying any extra clauses
    supplied in ``defaults`` (e.g. ``{"live_enabled": "DEFAULT 1"}``).
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if table_name not in insp.get_table_names():
        return
    existing = {c['name'] for c in insp.get_columns(table_name)}
    if column.name in existing:
        return

    type_sql = _column_type_sql(column, engine.dialect)
    extra = (defaults or {}).get(column.name, "")
    if extra:
        extra = " " + extra
    sql = f"ALTER TABLE {table_name} ADD COLUMN {column.name} {type_sql}{extra}"
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()


def _migrate_schema(engine):
    """Add missing columns to existing tables across all supported dialects."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)

    # Ensure helper tables exist (safe no-op if already present).
    tables = set(insp.get_table_names())
    if 'app_settings' not in tables:
        AppSetting.__table__.create(engine, checkfirst=True)
    if 'nt_accounts' not in tables:
        NtAccount.__table__.create(engine, checkfirst=True)
    if 'app_credentials' not in tables:
        AppCredential.__table__.create(engine, checkfirst=True)
    if 'line_trigger_state' not in tables:
        LineTriggerState.__table__.create(engine, checkfirst=True)
    if 'decision_logs' not in tables:
        DecisionLog.__table__.create(engine, checkfirst=True)

    # Trades table migrations
    if 'trades' in tables:
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.logs)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.risk_dollars)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.risk_pct)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.contracts)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.account_balance)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.fees)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.pnl_usd)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.source)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.account)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.signal_id)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.original_entry_price)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.gross_pnl)
        _add_column_if_missing(engine, 'trades', Trade.__table__.c.realized_pnl)

    # NT accounts migrations
    if 'nt_accounts' in tables:
        _add_column_if_missing(engine, 'nt_accounts', NtAccount.__table__.c.rr_ratio)
        _add_column_if_missing(
            engine, 'nt_accounts', NtAccount.__table__.c.live_enabled,
            defaults={"live_enabled": "DEFAULT 1"},
        )
        _add_column_if_missing(engine, 'nt_accounts', NtAccount.__table__.c.instrument_symbols)

        # Backfill live_enabled for legacy rows that have a NULL value.
        with engine.connect() as conn:
            conn.execute(text("UPDATE nt_accounts SET live_enabled = 1 WHERE live_enabled IS NULL"))
            conn.commit()


def setup_database(database_instance: DatabaseProtocol | None = None, db_url: str = "sqlite:///./database.db"):
    global db
    if database_instance is None:
        db = get_database(db_url=db_url)
    else:
        db = database_instance
    db.create_tables(Base)
    # Auto-migrate: add missing columns to existing tables for all dialects.
    # create_all only creates missing tables; it never adds missing columns.
    _migrate_schema(db.get_engine())
    return db
