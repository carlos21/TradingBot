from abc import ABC, abstractmethod

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

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
        # In-memory databases must share a single connection across threads,
        # otherwise each new connection gets a fresh empty database. File-based
        # databases use SQLAlchemy's default pool (QueuePool) so connections are
        # reused across threads, matching the original live-trading behavior.
        is_in_memory = ":memory:" in db_url or "mode=memory" in db_url
        pool_kwargs = {"poolclass": StaticPool} if is_in_memory else {}
        self.engine = create_engine(
            self.db_url,
            connect_args={"check_same_thread": False},
            **pool_kwargs,
            # SQLite optimizations for concurrent access
            execution_options={"sqlite_pragma": {"journal_mode": "WAL", "synchronous": "NORMAL"}}
        )
        # Apply WAL mode pragma on connect
        from sqlalchemy import event
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_conn, _connection_record):
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


def get_database(db_url: str = "sqlite:///./database.db"):
    return SQLiteDatabase(db_url=db_url)
