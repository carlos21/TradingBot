from abc import ABC, abstractmethod
from typing import Optional, List
from src.database.database import Line, get_db_session
from src.dbexception import DBException
from src.models import LineData
from datetime import datetime, timezone

import uuid


class LineRepository(ABC):

    @abstractmethod
    def insert_line(
        self,
        pair: str, 
        price: float,
        creation_date: Optional[datetime] = None
    ) -> LineData:
        pass

    @abstractmethod
    def list_lines(self, pair: str) -> List[LineData]:
        pass

    @abstractmethod
    def delete_line(self, line_id: str):
        pass


class SQLLineRepository(LineRepository):

    def _ensure_utc_aware(self, dt: datetime) -> datetime:
        """SQLite often strips timezone info; re-attach UTC when missing."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def insert_line(self, pair: str, price: float, creation_date: Optional[datetime] = None) -> LineData:
        with get_db_session() as db:
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
                raise DBException(message=str(e))
            finally:
                db.close()

            return LineData(
                line_id=new_line.line_id,
                pair=new_line.pair,
                price=new_line.price,
                creation_date=self._ensure_utc_aware(new_line.creation_date),
            )

    def list_lines(self, pair: str) -> List[LineData]:
        with get_db_session() as db:
            rows = (
                db.query(Line)
                  .filter(Line.pair == pair)
                  .all()
            )
            return [
                LineData(
                    line_id=row.line_id,
                    pair=row.pair,
                    price=row.price,
                    creation_date=self._ensure_utc_aware(row.creation_date),
                )
                for row in rows
            ]
         
    def delete_line(self, line_id: str) -> None:
        with get_db_session() as db:
            db.query(Line) \
                .filter(Line.line_id == line_id) \
                .delete()
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                raise DBException(message=str(e))
            finally:
                db.close()