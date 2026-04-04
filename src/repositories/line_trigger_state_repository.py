from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict

from src.dbexception import DBException


class LineTriggerStateRepository(ABC):
    """Persists per-line trigger state so it survives a restart."""

    @abstractmethod
    def save(self, line_id: str, pair: str, state: Dict[str, Any]) -> None:
        """Upsert the full state dict for a line."""

    @abstractmethod
    def load_all(self, pair: str) -> Dict[str, Dict[str, Any]]:
        """Return all persisted line states for a pair, keyed by line_id."""

    @abstractmethod
    def delete(self, line_id: str) -> None:
        """Remove a line's persisted state."""


class SQLiteLineTriggerStateRepository(LineTriggerStateRepository):

    def save(self, line_id: str, pair: str, state: Dict[str, Any]) -> None:
        from src.database.database import get_db_session, LineTriggerState as _ORM
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            existing = db.query(_ORM).filter_by(line_id=line_id).first()
            if existing:
                existing.state_json = dict(state)
                existing.updated_at = now
            else:
                db.add(_ORM(
                    line_id=line_id,
                    pair=pair,
                    state_json=dict(state),
                    updated_at=now,
                ))
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                raise DBException(message=str(e))

    def load_all(self, pair: str) -> Dict[str, Dict[str, Any]]:
        from src.database.database import get_db_session, LineTriggerState as _ORM
        with get_db_session() as db:
            rows = db.query(_ORM).filter_by(pair=pair).all()
            return {row.line_id: dict(row.state_json or {}) for row in rows}

    def delete(self, line_id: str) -> None:
        from src.database.database import get_db_session, LineTriggerState as _ORM
        with get_db_session() as db:
            db.query(_ORM).filter_by(line_id=line_id).delete()
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                raise DBException(message=str(e))


class InMemoryLineTriggerStateRepository(LineTriggerStateRepository):
    """No-op in-memory implementation for tests and backtest runs."""

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def save(self, line_id: str, pair: str, state: Dict[str, Any]) -> None:
        self._store[line_id] = dict(state)

    def load_all(self, pair: str) -> Dict[str, Dict[str, Any]]:
        return dict(self._store)

    def delete(self, line_id: str) -> None:
        self._store.pop(line_id, None)
