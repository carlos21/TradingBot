from abc import ABC, abstractmethod
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv, find_dotenv


Base = declarative_base()

class DatabaseProtocol(ABC):
    
    @abstractmethod
    def get_engine(self):
        pass

    @abstractmethod
    def get_session(self):
        pass

    @abstractmethod
    def create_tables(self, Base):
        pass


class SQLiteDatabase(DatabaseProtocol):
    
    def __init__(self, db_url="sqlite:///./database.db"):
        self.db_url = db_url
        # Enable connection pooling and WAL mode for better concurrent performance
        self.engine = create_engine(
            self.db_url, 
            connect_args={"check_same_thread": False},
            poolclass=None,  # Use NullPool for SQLite (connections can't be shared across threads)
            # SQLite optimizations for concurrent access
            execution_options={"sqlite_pragma": {"journal_mode": "WAL", "synchronous": "NORMAL"}}
        )
        # Apply WAL mode pragma on connect
        from sqlalchemy import event
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=10000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.close()
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_engine(self):
        return self.engine

    def get_session(self):
        return self.SessionLocal()

    def create_tables(self, Base):
        Base.metadata.create_all(bind=self.engine)


def get_database():
    return SQLiteDatabase()