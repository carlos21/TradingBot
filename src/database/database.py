from contextlib import contextmanager
from src.database.database_protocol import Base, get_database
from sqlalchemy import Column, String, DateTime, JSON, Float, Integer, Text, func, Index


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
    stop_loss    = Column(Float,   nullable=False)
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


db = None

@contextmanager
def get_db_session():
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
            # logger removed - pass via constructor if needed
