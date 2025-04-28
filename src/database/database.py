from contextlib import contextmanager
from src.database.database_protocol import Base, get_database
from sqlalchemy import Column, String, DateTime, JSON, Float


class Line(Base):
    __tablename__ = "lines"

    line_id = Column(String(50), primary_key=True, index=False)
    pair = Column(String(10), nullable=False)
    price = Column(Float, nullable=False)
    direction = Column(String(10), nullable=False)
    creation_date = Column(DateTime, nullable=False)

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
