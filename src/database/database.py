from contextlib import contextmanager
from src.database.database_protocol import Base, get_database
from sqlalchemy import Column, String, DateTime, JSON, Float, func


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
    result_type  = Column(String(10), nullable=True)    # "SL", "TP", or "SP"
    fees         = Column(Float,   nullable=True)
    pnl_usd      = Column(Float,   nullable=True)
    params       = Column(JSON,    nullable=True)       # any extra metadata (e.g. {"rr": "1:4"})
    logs         = Column(JSON,    nullable=True)       # per-trade lifecycle log entries
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LineTriggerState(Base):
    __tablename__ = "line_trigger_state"

    line_id    = Column(String(50), primary_key=True)
    pair       = Column(String(10), nullable=False)
    state_json = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), nullable=False)


db = None

@contextmanager
def get_db_session():
    db_session = db.get_session()
    try:
        yield db_session
    finally:
        db_session.close()

def setup_database():
    global db
    db = get_database()
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
            print("[DB] Auto-migrated: added 'logs' column to trades table")
        if 'risk_dollars' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN risk_dollars FLOAT"))
                conn.execute(text("ALTER TABLE trades ADD COLUMN risk_pct FLOAT"))
                conn.execute(text("ALTER TABLE trades ADD COLUMN contracts FLOAT"))
                conn.commit()
            print("[DB] Auto-migrated: added 'risk_dollars', 'risk_pct', 'contracts' columns to trades table")
        elif 'contracts' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN contracts FLOAT"))
                conn.commit()
            print("[DB] Auto-migrated: added 'contracts' column to trades table")
        if 'fees' not in columns:
            with db.get_engine().connect() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN fees FLOAT"))
                conn.execute(text("ALTER TABLE trades ADD COLUMN pnl_usd FLOAT"))
                conn.commit()
            print("[DB] Auto-migrated: added 'fees', 'pnl_usd' columns to trades table")
