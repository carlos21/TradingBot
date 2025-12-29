from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime, timezone
from src.database.database import Trade, Line, get_db_session
from src.dbexception import DBException, DBNotFoundException
from src.models import TradeData

import uuid


class TradeRepository(ABC):

    @abstractmethod
    def insert_trade(
        self,
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

    def _ensure_utc(self, dt: Optional[datetime]) -> Optional[datetime]:
        """Helper to ensure a datetime is UTC-aware."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def insert_trade(
        self,
        pair: str,
        trade_type: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk: float,
        entry_time: datetime,
        params: Optional[dict] = None
    ) -> TradeData:
        with get_db_session() as db:
            t = Trade(
                trade_id=str(uuid.uuid4()),
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

        # manual TradeData construction
        return TradeData(
            trade_id=t.trade_id,
            pair=t.pair,
            trade_type=t.trade_type,
            entry_price=t.entry_price,
            stop_loss=t.stop_loss,
            take_profit=t.take_profit,
            risk=t.risk,
            entry_time=self._ensure_utc(t.entry_time),
            exit_price=t.exit_price,
            exit_time=self._ensure_utc(t.exit_time),
            result=t.result,
            params=t.params,
            created_at=self._ensure_utc(t.created_at)
        )

    def list_trades(self, pair: str) -> List[TradeData]:
        with get_db_session() as db:
            rows = db.query(Trade).filter(Trade.pair == pair).all()
            result: List[TradeData] = []
            for t in rows:
                result.append(
                    TradeData(
                        trade_id=t.trade_id,
                        pair=t.pair,
                        trade_type=t.trade_type,
                        entry_price=t.entry_price,
                        stop_loss=t.stop_loss,
                        take_profit=t.take_profit,
                        risk=t.risk,
                        entry_time=self._ensure_utc(t.entry_time),
                        exit_price=t.exit_price,
                        exit_time=self._ensure_utc(t.exit_time),
                        result=t.result,
                        params=t.params,
                        created_at=self._ensure_utc(t.created_at)
                    )
                )
            db.close()
            return result

    def update_stop_loss(self, trade_id: str, new_stop_loss: float) -> TradeData:
        with get_db_session() as db:
            t = db.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
            if not t:
                db.close()
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

        return TradeData(
            trade_id=t.trade_id,
            pair=t.pair,
            trade_type=t.trade_type,
            entry_price=t.entry_price,
            stop_loss=t.stop_loss,
            take_profit=t.take_profit,
            risk=t.risk,
            entry_time=self._ensure_utc(t.entry_time),
            exit_price=t.exit_price,
            exit_time=self._ensure_utc(t.exit_time),
            result=t.result,
            params=t.params,
            created_at=self._ensure_utc(t.created_at)
        )

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
                db.close()
                raise DBNotFoundException(f"Trade {trade_id} not found")
            t.exit_price = exit_price
            t.exit_time = exit_time
            t.result = result
            try:
                db.commit()
                db.refresh(t)
            except Exception as e:
                db.rollback()
                raise DBException(str(e))
            finally:
                db.close()

        return TradeData(
            trade_id=t.trade_id,
            pair=t.pair,
            trade_type=t.trade_type,
            entry_price=t.entry_price,
            stop_loss=t.stop_loss,
            take_profit=t.take_profit,
            risk=t.risk,
            entry_time=self._ensure_utc(t.entry_time),
            exit_price=t.exit_price,
            exit_time=self._ensure_utc(t.exit_time),
            result=t.result,
            params=t.params,
            created_at=self._ensure_utc(t.created_at)
        )