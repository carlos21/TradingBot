from abc import ABC, abstractmethod
from typing import Optional, List
from src.database.database import Line, get_db_session
from src.models import LineData
from datetime import datetime, timezone

import uuid


class LineRepository(ABC):

    @abstractmethod
    def insert_line(
        self,
        pair: str, 
        price,
        direction: str
    ) -> LineData:
        pass

    @abstractmethod
    def list_lines(self) -> List[LineData]:
        pass

    @abstractmethod
    def delete_line(self, line_id: str):
        pass


class SQLLineRepository(LineRepository):
    
    def insert_line(self, pair: str, price, direction: str) -> LineData:
        with get_db_session() as db:
            new_line = Line(
                line_id=str(uuid.uuid4()),
                pair=pair,
                price=price,
                direction=direction,
                creation_date=datetime.utcnow()
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
                direction=new_line.direction,
                creation_date=new_line.creation_date
            )

    def list_lines(self) -> List[LineData]:
         with get_db_session() as db:
            rows = db.query(Line).all()
            return [
                LineData(
                    line_id=row.line_id,
                    pair=row.pair,
                    price=row.price,
                    direction=row.direction,
                    creation_date=row.creation_date
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
        
class DBException(Exception):

    def __init__(self, message="Operation failed"):
        self.message = message
        super().__init__(self.message)

class DBNotFoundException(Exception):

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)