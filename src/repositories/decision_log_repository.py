"""Decision log repository for persistent signal/trade decision logging."""

from typing import List, Optional
from datetime import datetime, timezone, timedelta
from threading import Lock

from src.database.database import DecisionLog, get_db_session


class DecisionLogRepository:
    """Repository for persisting strategy decision logs to SQLite.

    Each decision (latch, trigger skip, filter block, entry, etc.) is stored
    as a row so the full lifecycle of a line can be audited after the fact.
    """

    def __init__(self):
        self._lock = Lock()

    def add_log(
        self,
        *,
        bar_time: float,
        pair: str,
        tf: Optional[str] = None,
        line_id: Optional[str] = None,
        event: str,
        direction: Optional[str] = None,
        trigger_name: Optional[str] = None,
        filter_name: Optional[str] = None,
        reason: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        """Insert a single decision log."""
        with self._lock:
            with get_db_session() as db:
                log = DecisionLog(
                    bar_time=bar_time,
                    pair=pair,
                    tf=tf,
                    line_id=line_id,
                    event=event,
                    direction=direction,
                    trigger_name=trigger_name,
                    filter_name=filter_name,
                    reason=reason,
                    details=details,
                )
                db.add(log)
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()

    def get_recent(
        self,
        pair: Optional[str] = None,
        event: Optional[str] = None,
        line_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[dict]:
        """Query recent decision logs with optional filters."""
        with get_db_session() as db:
            query = db.query(DecisionLog).order_by(DecisionLog.id.desc())
            if pair:
                query = query.filter(DecisionLog.pair == pair)
            if event:
                query = query.filter(DecisionLog.event == event)
            if line_id:
                query = query.filter(DecisionLog.line_id == line_id)
            rows = query.limit(limit).all()
            result = [self._to_dict(r) for r in rows]
            db.close()
            return result

    def get_by_line_id(self, line_id: str) -> List[dict]:
        """Full audit trail for a single strategy line."""
        with get_db_session() as db:
            rows = (
                db.query(DecisionLog)
                .filter(DecisionLog.line_id == line_id)
                .order_by(DecisionLog.bar_time.asc())
                .all()
            )
            result = [self._to_dict(r) for r in rows]
            db.close()
            return result

    def cleanup_old(self, days: int = 30) -> int:
        """Delete logs older than N days. Returns number of rows deleted."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._lock:
            with get_db_session() as db:
                count = (
                    db.query(DecisionLog)
                    .filter(DecisionLog.created_at < cutoff)
                    .delete(synchronize_session=False)
                )
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()
                return count

    @staticmethod
    def _to_dict(row: DecisionLog) -> dict:
        return {
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "bar_time": row.bar_time,
            "pair": row.pair,
            "tf": row.tf,
            "line_id": row.line_id,
            "event": row.event,
            "direction": row.direction,
            "trigger_name": row.trigger_name,
            "filter_name": row.filter_name,
            "reason": row.reason,
            "details": row.details,
        }
