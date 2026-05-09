"""Base class for SQL-backed repositories.

Provides shared session-management logic so individual repositories don't
need to copy-paste the same ``_session()`` context manager.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from src.infrastructure.database.database import get_db_session
from src.infrastructure.database.database_protocol import DatabaseProtocol


class SQLRepositoryBase:
    """Base for repositories that open a SQL session per operation.

    Subclasses receive an optional ``DatabaseProtocol`` instance. When one is
    provided the repository is *injected* and fully testable. When ``None`` is
    passed the repository falls back to the global ``get_db_session()``
    helper for backward compatibility with legacy code.
    """

    def __init__(self, db: DatabaseProtocol | None = None):
        self._db = db

    @contextmanager
    def _session(self) -> Generator:
        if self._db is not None:
            session = self._db.get_session()
            try:
                yield session
            finally:
                session.close()
        else:
            with get_db_session() as session:
                yield session

    def _engine(self):
        """Return a SQLAlchemy engine (direct-SQL fast paths)."""
        if self._db is not None:
            return self._db.get_engine()
        from src.infrastructure.database.database import db as global_db
        if global_db is None:
            raise RuntimeError("Database not initialized.")
        return global_db.get_engine()
