"""Base class for SQL-backed repositories.

Provides shared session-management logic so individual repositories don't
need to copy-paste the same ``_session()`` context manager.
"""

from __future__ import annotations

import functools
import random
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, TypeVar

from sqlalchemy.exc import OperationalError

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

    def _is_sqlite_busy(self, exc: Exception) -> bool:
        """Return True if ``exc`` is a SQLite 'database is locked' error."""
        if not isinstance(exc, OperationalError):
            return False
        # SQLAlchemy wraps the DBAPI exception; the original is available
        # via __cause__ or the first arg string.
        msg = str(exc).lower()
        cause = exc.__cause__
        if cause is not None:
            msg += " " + str(cause).lower()
        return "database is locked" in msg


T = TypeVar("T")


def retry_on_sqlite_lock(
    max_retries: int = 3,
    base_delay: float = 0.05,
    max_delay: float = 1.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that retries a function on transient SQLite 'database is locked'.

    The retry uses capped exponential backoff with a small jitter to avoid
    thundering-herd collisions between concurrent writers.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except OperationalError as exc:
                    last_exc = exc
                    # Only retry SQLite lock errors; re-raise anything else immediately.
                    is_busy = "database is locked" in str(exc).lower()
                    cause = exc.__cause__
                    if cause is not None:
                        is_busy = is_busy or "database is locked" in str(cause).lower()
                    if not is_busy or attempt == max_retries:
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    delay += random.uniform(0, 0.02)
                    time.sleep(delay)
            # Defensive fallback; should never be reached.
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("retry loop exited without result")

        return wrapper

    return decorator
