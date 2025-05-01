from contextlib import contextmanager
from src.database.database_protocol import Base, get_database
from sqlalchemy import Column, String, DateTime, JSON, Float, func


class Line(Base):
    __tablename__ = "lines"

    line_id = Column(String(50), primary_key=True, index=False)
    pair = Column(String(10), nullable=False)
    price = Column(Float, nullable=False)
    direction = Column(String(10), nullable=False)
    creation_date = Column(DateTime, nullable=False)

class Trade(Base):
    __tablename__ = "trades"

    trade_id     = Column(String(50), primary_key=True, index=False)
    pair         = Column(String(10), nullable=False)
    trade_type   = Column(String(10), nullable=False)   # "long" or "short"
    entry_price  = Column(Float,   nullable=False)
    stop_loss    = Column(Float,   nullable=False)
    take_profit  = Column(Float,   nullable=False)
    risk         = Column(Float,   nullable=False)
    entry_time   = Column(DateTime(timezone=True), nullable=False)
    exit_price   = Column(Float,   nullable=True)
    exit_time    = Column(DateTime(timezone=True), nullable=True)
    result       = Column(Float,   nullable=True)       # e.g. PnL or +1/–1 flag
    params       = Column(JSON,    nullable=True)       # any extra metadata (e.g. {"rr": "1:4"})
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

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
