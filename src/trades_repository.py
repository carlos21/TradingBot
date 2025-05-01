from abc import ABC, abstractmethod
from typing import List
from datetime import datetime
from src.database.database import Trade, Line, get_db_session
from src.dbexception import DBException, DBNotFoundException
from src.models import TradeData

import uuid


class TradeRepository(ABC):

    @abstractmethod
    def insert_trade(
        self,
        line_id: str,
        pair: str,
        trade_type: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk: float,
        entry_time: datetime,
        params: dict | None = None
    ) -> TradeData:
        """Create & persist a new Trade."""
        pass

    @abstractmethod
    def list_trades(self, pair: str) -> List[TradeData]:
        """Return all trades for a given symbol."""
        pass

    @abstractmethod
    def update_stop_loss(self, trade_id: str, new_stop_loss: float) -> TradeData:
        """Adjust an existing trade’s stop loss."""
        pass

    @abstractmethod
    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_time: datetime,
        result: float
    ) -> TradeData:
        """Mark a trade as closed, recording exit details."""
        pass


class SQLTradeRepository(TradeRepository):

    def insert_trade(
        self,
        line_id: str,
        pair: str,
        trade_type: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk: float,
        entry_time: datetime,
        params: dict | None = None
    ) -> TradeData:
        with get_db_session() as db:
            t = Trade(
                trade_id=str(uuid.uuid4()),
                line_id=line_id,
                pair=pair,
                trade_type=trade_type,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk=risk,
                entry_time=entry_time,
                params=params or {}
            )
            db.add(t)
            try:
                db.commit()
                db.refresh(t)
            except Exception as e:
                db.rollback()
                raise DBException(str(e))
            finally:
                db.close()

            return TradeData.from_orm(t)


    def list_trades(self, pair: str) -> List[TradeData]:
        with get_db_session() as db:
            rows = db.query(Trade).filter(Trade.pair == pair).all()
            return [TradeData.from_orm(r) for r in rows]


    def update_stop_loss(self, trade_id: str, new_stop_loss: float) -> TradeData:
        with get_db_session() as db:
            t = db.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
            if not t:
                raise DBNotFoundException(f"Trade {trade_id} not found")
            t.stop_loss = new_stop_loss
            try:
                db.commit()
                db.refresh(t)
            except Exception as e:
                db.rollback()
                raise DBException(str(e))
            finally:
                db.close()
            return TradeData.from_orm(t)


    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_time: datetime,
        result: float
    ) -> TradeData:
        with get_db_session() as db:
            t = db.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
            if not t:
                raise DBNotFoundException(f"Trade {trade_id} not found")
            t.exit_price = exit_price
            t.exit_time  = exit_time
            t.result     = result
            try:
                db.commit()
                db.refresh(t)
            except Exception as e:
                db.rollback()
                raise DBException(str(e))
            finally:
                db.close()
            return TradeData.from_orm(t)