from contextlib import contextmanager

from sqlalchemy import JSON, Column, DateTime, Float, Index, Integer, String, Text, func

from src.infrastructure.database.database_protocol import Base, get_database


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
    contracts    = Column(Float,   nullable=True)
    entry_time   = Column(DateTime(timezone=True), nullable=False)
    exit_price   = Column(Float,   nullable=True)
    exit_time    = Column(DateTime(timezone=True), nullable=True)
    result       = Column(Float,   nullable=True)       # e.g. PnL or +1/–1 flag
    result_type  = Column(String(10), nullable=True)    # "TP", "SL", "BE", or "SP"
    fees         = Column(Float,   nullable=True)
    pnl_usd      = Column(Float,   nullable=True)
    params       = Column(JSON,    nullable=True)       # any extra metadata (e.g. {"rr": "1:4"})
    logs         = Column(JSON,    nullable=True)       # per-trade lifecycle log entries
    source       = Column(String(20), nullable=True)    # strategy, manual, test, broker_sync
    account      = Column(String(50), nullable=True)    # NT account name (NULL for signal trades)
    signal_id    = Column(String(50), nullable=True)    # parent signal trade_id for multi-account expansion
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LineTriggerState(Base):
    __tablename__ = "line_trigger_state"

    line_id    = Column(String(50), primary_key=True)
    pair       = Column(String(10), nullable=False)
    state_json = Column(JSON, nullable=False, default=lambda: {})
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

    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String(100), nullable=False, unique=True)
    risk_usd   = Column(Float, nullable=True)
    risk_pct   = Column(Float, nullable=True)
    rr_ratio   = Column(Float, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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

def setup_database(db_url: str = "sqlite:///./database.db"):
    global db
    db = get_database(db_url=db_url)
    db.create_tables(Base)
    # Auto-migrate: add 'logs' column if missing (added after initial schema)
    from sqlalchemy import inspect, text
    insp = inspect(db.get_engine())
    if 'trades' in insp.get_table_names():
        columns = [c['name'] for c in insp.get_columns('trades')]
        if 'logs' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN logs JSON"))
                conn.commit()
            # logger removed - pass via constructor if needed
        if 'risk_dollars' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN risk_dollars FLOAT"))
                conn.execute(text("ALTER TABLE trades ADD COLUMN risk_pct FLOAT"))
                conn.execute(text("ALTER TABLE trades ADD COLUMN contracts FLOAT"))
                conn.commit()
            # logger removed - pass via constructor if needed
        elif 'contracts' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN contracts FLOAT"))
                conn.commit()
            # logger removed - pass via constructor if needed
        if 'fees' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN fees FLOAT"))
                conn.execute(text("ALTER TABLE trades ADD COLUMN pnl_usd FLOAT"))
                conn.commit()
            # logger removed - pass via constructor if needed
        if 'source' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN source VARCHAR(20) DEFAULT 'strategy'"))
                conn.commit()
        if 'account' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN account VARCHAR(50)"))
                conn.commit()
        if 'signal_id' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN signal_id VARCHAR(50)"))
                conn.commit()

    # Auto-create new tables for settings/accounts/credentials if missing
    tables = insp.get_table_names()
    if 'app_settings' not in tables:
        AppSetting.__table__.create(db.get_engine(), checkfirst=True)
    if 'nt_accounts' not in tables:
        NtAccount.__table__.create(db.get_engine(), checkfirst=True)
    else:
        # Auto-migrate: add rr_ratio column if missing
        nt_columns = [c['name'] for c in insp.get_columns('nt_accounts')]
        if 'rr_ratio' not in nt_columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE nt_accounts ADD COLUMN rr_ratio FLOAT"))
                conn.commit()
    if 'app_credentials' not in tables:
        AppCredential.__table__.create(db.get_engine(), checkfirst=True)
