"""Line trigger state repository implementations.

Provides both SQL and in-memory implementations for persisting trigger state.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.dbexception import DBException
from src.repositories.interfaces import LineTriggerStateRepository as ILineTriggerStateRepository


class SQLiteLineTriggerStateRepository(ILineTriggerStateRepository):
    """SQL-based trigger state repository."""

    def save(self, line_id: str, pair: str, state: Dict[str, Any]) -> None:
        import json
        from sqlalchemy import text
        from src.database.database import get_db_session
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            # Use INSERT OR REPLACE to avoid SELECT-then-UPDATE race conditions
            db.execute(
                text(
                    """
                    INSERT INTO line_trigger_state (line_id, pair, state_json, updated_at)
                    VALUES (:line_id, :pair, :state_json, :updated_at)
                    ON CONFLICT(line_id) DO UPDATE SET
                        pair = excluded.pair,
                        state_json = excluded.state_json,
                        updated_at = excluded.updated_at
                    """
                ),
                {
                    "line_id": line_id,
                    "pair": pair,
                    "state_json": json.dumps(state),
                    "updated_at": now.isoformat(),
                },
            )
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                raise DBException(message=str(e))

    def load(self, line_id: str) -> Optional[Dict[str, Any]]:
        """Load state for a single line."""
        from src.database.database import get_db_session, LineTriggerState as _ORM
        with get_db_session() as db:
            row = db.query(_ORM).filter_by(line_id=line_id).first()
            if row:
                return dict(row.state_json or {})
            return None

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


class InMemoryLineTriggerStateRepository(ILineTriggerStateRepository):
    """No-op in-memory implementation for tests and backtest runs."""

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def save(self, line_id: str, pair: str, state: Dict[str, Any]) -> None:
        self._store[line_id] = dict(state)

    def load(self, line_id: str) -> Optional[Dict[str, Any]]:
        """Load state for a single line."""
        return self._store.get(line_id)

    def load_all(self, pair: str) -> Dict[str, Dict[str, Any]]:
        return dict(self._store)

    def delete(self, line_id: str) -> None:
        self._store.pop(line_id, None)


# Backward compatibility alias
LineTriggerStateRepository = ILineTriggerStateRepository
