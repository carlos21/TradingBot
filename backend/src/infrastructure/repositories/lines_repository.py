"""Line repository implementations.

Provides both the standard repository (per-operation sessions) and
the Unit of Work compatible implementation.
"""

import uuid
from datetime import datetime, timezone

from src.dbexception import DBException, DBNotFoundException
from src.domain.models import LineData
from src.domain.repositories import LineRepository as ILineRepository
from src.infrastructure.database.database import Line
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.repositories.base import SQLRepositoryBase, retry_on_sqlite_lock


class SQLLineRepository(SQLRepositoryBase, ILineRepository):
    """SQL-based line repository with per-operation sessions.

    Each operation opens and closes its own database session.
    For multi-operation transactions, use UnitOfWork instead.
    """

    def __init__(self, db: DatabaseProtocol | None = None):
        super().__init__(db)

    def _ensure_utc_aware(self, dt: datetime) -> datetime:
        """SQLite often strips timezone info; re-attach UTC when missing."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _make_line_data(self, line: Line) -> LineData:
        """Convert Line ORM object to LineData DTO."""
        return LineData(
            line_id=line.line_id,
            pair=line.pair,
            price=line.price,
            creation_date=self._ensure_utc_aware(line.creation_date),
        )

    @retry_on_sqlite_lock()
    def insert_line(self, pair: str, price: float, creation_date: datetime | None = None) -> LineData:
        with self._session() as db:
            c_date = creation_date if creation_date else datetime.now(timezone.utc)

            new_line = Line(
                line_id=str(uuid.uuid4()),
                pair=pair,
                price=price,
                creation_date=c_date
            )
            db.add(new_line)

            try:
                db.commit()
                db.refresh(new_line)
            except Exception as e:
                db.rollback()
                raise DBException(message=str(e)) from e
            finally:
                db.close()

            return self._make_line_data(new_line)

    def get_line(self, line_id: str) -> LineData | None:
        with self._session() as db:
            row = db.query(Line).filter(Line.line_id == line_id).one_or_none()
            if not row:
                return None
            return self._make_line_data(row)

    def list_lines(self, pair: str) -> list[LineData]:
        with self._session() as db:
            rows = (
                db.query(Line)
                  .filter(Line.pair == pair)
                  .all()
            )
            return [self._make_line_data(row) for row in rows]

    @retry_on_sqlite_lock()
    def update_line(self, line_id: str, price: float) -> LineData:
        with self._session() as db:
            row = db.query(Line).filter(Line.line_id == line_id).one_or_none()
            if not row:
                db.close()
                raise DBNotFoundException(f"Line {line_id} not found")
            row.price = price
            try:
                db.commit()
                db.refresh(row)
            except Exception as e:
                db.rollback()
                raise DBException(message=str(e)) from e
            finally:
                db.close()

            return self._make_line_data(row)

    @retry_on_sqlite_lock()
    def delete_line(self, line_id: str) -> None:
        with self._session() as db:
            db.query(Line) \
                .filter(Line.line_id == line_id) \
                .delete()
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                raise DBException(message=str(e)) from e
            finally:
                db.close()


