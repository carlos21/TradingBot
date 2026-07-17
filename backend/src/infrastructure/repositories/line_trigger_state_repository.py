"""Line trigger state repository implementations.

Provides both SQL and in-memory implementations for persisting trigger state.
"""

from datetime import datetime, timezone
from typing import Any

from src.dbexception import DBException
from src.domain.repositories import (
    LineTriggerStateRepository as ILineTriggerStateRepository,
)
from src.infrastructure.database.database import LineTriggerState
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.repositories.base import SQLRepositoryBase, retry_on_sqlite_lock


class SQLiteLineTriggerStateRepository(SQLRepositoryBase, ILineTriggerStateRepository):
    """SQL-based trigger state repository."""

    def __init__(self, db: DatabaseProtocol | None = None):
        super().__init__(db)

    @retry_on_sqlite_lock()
    def save(self, line_id: str, pair: str, state: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        with self._session() as db:
            # Portable ORM upsert. SQLite has native UPSERT syntax, but it is
            # not portable to PostgreSQL/MySQL without dialect-specific SQL.
            row = db.query(LineTriggerState).filter_by(line_id=line_id).first()
            if row is None:
                row = LineTriggerState(
                    line_id=line_id,
                    pair=pair,
                    state_json=state,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.pair = pair
                row.state_json = state
                row.updated_at = now
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                raise DBException(message=str(e)) from e

    def _parse_state(self, raw: Any) -> dict[str, Any]:
        """Normalize a JSON/state value to a Python dict."""
        import json

        if raw is None:
            return {}
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                return dict(json.loads(raw))
            except Exception:
                return {}
        return {}

    def load(self, line_id: str) -> dict[str, Any] | None:
        """Load state for a single line."""
        from src.infrastructure.database.database import LineTriggerState as _ORM
        with self._session() as db:
            row = db.query(_ORM).filter_by(line_id=line_id).first()
            if row:
                return self._parse_state(row.state_json)
            return None

    def load_all(self, pair: str) -> dict[str, dict[str, Any]]:
        from src.infrastructure.database.database import LineTriggerState as _ORM
        with self._session() as db:
            rows = db.query(_ORM).filter_by(pair=pair).all()
            return {row.line_id: self._parse_state(row.state_json) for row in rows}

    @retry_on_sqlite_lock()
    def delete(self, line_id: str) -> None:
        from src.infrastructure.database.database import LineTriggerState as _ORM
        with self._session() as db:
            db.query(_ORM).filter_by(line_id=line_id).delete()
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                raise DBException(message=str(e)) from e


class InMemoryLineTriggerStateRepository(ILineTriggerStateRepository):
    """No-op in-memory implementation for tests and backtest runs."""

    def __init__(self):
        self._store: dict[str, dict[str, Any]] = {}

    def save(self, line_id: str, _pair: str, state: dict[str, Any]) -> None:
        self._store[line_id] = dict(state)

    def load(self, line_id: str) -> dict[str, Any] | None:
        """Load state for a single line."""
        return self._store.get(line_id)

    def load_all(self, _pair: str) -> dict[str, dict[str, Any]]:
        return dict(self._store)

    def delete(self, line_id: str) -> None:
        self._store.pop(line_id, None)


