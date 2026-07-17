from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import create_engine, event
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

    @property
    @abstractmethod
    def dialect(self) -> str:
        """Return the SQLAlchemy dialect name for this database."""
        pass

    @property
    def is_sqlite(self) -> bool:
        return self.dialect == "sqlite"


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
            connect_args={
                "check_same_thread": False,
                # Wait up to 30 seconds before raising "database is locked".
                # This gives concurrent writers a fair chance to serialize
                # instead of failing immediately.
                "timeout": 30.0,
            },
            **pool_kwargs,
            # SQLite optimizations for concurrent access
            execution_options={"sqlite_pragma": {"journal_mode": "WAL", "synchronous": "NORMAL"}}
        )
        # Apply WAL mode pragma on connect
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
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

    @property
    def dialect(self) -> str:
        return "sqlite"


class PostgreSQLDatabase(DatabaseProtocol):
    """PostgreSQL-backed database.

    Expects a SQLAlchemy URL such as ``postgresql://user:pass@host/db`` or
    ``postgresql+psycopg://user:pass@host/db``. Requires the ``psycopg`` or
    ``psycopg2`` driver to be installed.
    """

    def __init__(self, db_url: str = "postgresql://user:pass@localhost/tradingbot"):
        self.db_url = db_url
        self.engine = create_engine(
            db_url,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_engine(self):
        return self.engine

    def get_session(self):
        return self.SessionLocal()

    def create_tables(self, Base):
        Base.metadata.create_all(bind=self.engine)

    @property
    def dialect(self) -> str:
        return "postgresql"


class MySQLDatabase(DatabaseProtocol):
    """MySQL / MariaDB-backed database.

    Expects a SQLAlchemy URL such as ``mysql+pymysql://user:pass@host/db`` or
    ``mariadb+pymysql://user:pass@host/db``. Requires the ``pymysql`` driver
    to be installed.
    """

    def __init__(self, db_url: str = "mysql+pymysql://user:pass@localhost/tradingbot"):
        self.db_url = db_url
        self.engine = create_engine(
            db_url,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_engine(self):
        return self.engine

    def get_session(self):
        return self.SessionLocal()

    def create_tables(self, Base):
        Base.metadata.create_all(bind=self.engine)

    @property
    def dialect(self) -> str:
        return "mysql"


def get_database(db_url: str = "sqlite:///./database.db") -> DatabaseProtocol:
    """Factory that returns the correct DatabaseProtocol for a SQLAlchemy URL."""
    lower = db_url.lower()
    if lower.startswith(("postgresql://", "postgresql+", "postgres://", "postgres+")):
        return PostgreSQLDatabase(db_url=db_url)
    if lower.startswith(("mysql://", "mysql+", "mariadb://", "mariadb+")):
        return MySQLDatabase(db_url=db_url)
    return SQLiteDatabase(db_url=db_url)
