"""Trade repository implementations.

Provides both the standard repository (per-operation sessions) and
the Unit of Work compatible implementation.
"""

from typing import List, Optional
from datetime import datetime, timezone
from threading import Lock

from src.database.database import Trade, get_db_session
from src.dbexception import DBException, DBNotFoundException
from src.models import TradeData
from src.repositories.interfaces import TradeRepository as ITradeRepository

import uuid


class SQLTradeRepository(ITradeRepository):
    """SQL-based trade repository with per-operation sessions.
    
    Each operation opens and closes its own database session.
    For multi-operation transactions, use UnitOfWork instead.
    """

    def __init__(self):
        self._log_lock = Lock()

    def _ensure_utc(self, dt: Optional[datetime]) -> Optional[datetime]:
        """Helper to ensure a datetime is UTC-aware."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _make_trade_data(self, t: Trade) -> TradeData:
        """Convert Trade ORM object to TradeData DTO."""
        return TradeData(
            trade_id=t.trade_id,
            pair=t.pair,
            trade_type=t.trade_type,
            entry_price=t.entry_price,
            stop_loss=t.stop_loss,
            take_profit=t.take_profit,
            risk=t.risk,
            risk_dollars=t.risk_dollars,
            risk_pct=t.risk_pct,
            contracts=t.contracts,
            entry_time=self._ensure_utc(t.entry_time),
            exit_price=t.exit_price,
            exit_time=self._ensure_utc(t.exit_time),
            result=t.result,
            result_type=t.result_type,
            fees=t.fees,
            pnl_usd=t.pnl_usd,
            params=t.params,
            logs=t.logs or [],
            created_at=self._ensure_utc(t.created_at),
        )

    def insert_trade(
        self,
        pair: str,
        trade_type: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk: float,
        entry_time: datetime,
        params: Optional[dict] = None,
        risk_dollars: Optional[float] = None,
        risk_pct: Optional[float] = None,
        contracts: Optional[float] = None,
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
                risk_dollars=risk_dollars,
                risk_pct=risk_pct,
                contracts=contracts,
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

        return self._make_trade_data(t)

    def list_trades(self, pair: str) -> List[TradeData]:
        with get_db_session() as db:
            rows = db.query(Trade).filter(Trade.pair == pair).all()
            result: List[TradeData] = []
            for t in rows:
                result.append(self._make_trade_data(t))
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

        return self._make_trade_data(t)

    def update_take_profit(self, trade_id: str, new_take_profit: float) -> TradeData:
        with get_db_session() as db:
            t = db.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
            if not t:
                db.close()
                raise DBNotFoundException(f"Trade {trade_id} not found")
            t.take_profit = new_take_profit
            try:
                db.commit()
                db.refresh(t)
            except Exception as e:
                db.rollback()
                raise DBException(str(e))
            finally:
                db.close()

        return self._make_trade_data(t)

    def update_entry_price(self, trade_id: str, new_entry_price: float) -> TradeData:
        with get_db_session() as db:
            t = db.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
            if not t:
                db.close()
                raise DBNotFoundException(f"Trade {trade_id} not found")
            t.entry_price = new_entry_price
            try:
                db.commit()
                db.refresh(t)
            except Exception as e:
                db.rollback()
                raise DBException(str(e))
            finally:
                db.close()

        return self._make_trade_data(t)

    def update_risk_fields(self, trade_id: str, risk: float, risk_dollars: float, risk_pct: float) -> TradeData:
        with get_db_session() as db:
            t = db.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
            if not t:
                db.close()
                raise DBNotFoundException(f"Trade {trade_id} not found")
            t.risk = risk
            t.risk_dollars = risk_dollars
            t.risk_pct = risk_pct
            try:
                db.commit()
                db.refresh(t)
            except Exception as e:
                db.rollback()
                raise DBException(str(e))
            finally:
                db.close()

        return self._make_trade_data(t)

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_time: datetime,
        result: float,
        result_type: Optional[str] = None,
        fees: Optional[float] = None,
        pnl_usd: Optional[float] = None,
    ) -> TradeData:
        with get_db_session() as db:
            t = db.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
            if not t:
                db.close()
                raise DBNotFoundException(f"Trade {trade_id} not found")
            t.exit_price = exit_price
            t.exit_time = exit_time
            t.result = result
            t.result_type = result_type
            t.fees = fees
            t.pnl_usd = pnl_usd
            try:
                db.commit()
                db.refresh(t)
            except Exception as e:
                db.rollback()
                raise DBException(str(e))
            finally:
                db.close()

        return self._make_trade_data(t)

    def append_trade_log(self, trade_id: str, event: str, message: str) -> None:
        """Append a log entry to the trade's logs column.
        
        Optimized to minimize lock contention and DB round-trips.
        """
        import json
        from sqlalchemy import text
        from src.database.database import db
        
        log_entry = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "event": event,
            "msg": message,
        }
        log_json = json.dumps(log_entry)
        
        # Fast path: direct SQL JSON append using SQLite JSON1 extension
        try:
            with db.get_engine().connect() as conn:
                # First try: use json_insert to append to array
                result = conn.execute(
                    text("""
                        UPDATE trades 
                        SET logs = CASE 
                            WHEN logs IS NULL OR json_type(logs) IS NULL 
                            THEN json_array(:log_entry)
                            ELSE json_insert(logs, '$[#]', json(:log_entry))
                        END
                        WHERE trade_id = :trade_id
                    """),
                    {"log_entry": log_json, "trade_id": trade_id}
                )
                conn.commit()
                if result.rowcount > 0:
                    return
        except Exception:
            pass  # Fall through to ORM method
        
        # Fallback: use ORM approach with minimal lock time
        with self._log_lock:
            with get_db_session() as db_session:
                t = db_session.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
                if not t:
                    return
                logs = list(t.logs or [])
                logs.append(log_entry)
                t.logs = logs
                try:
                    db_session.commit()
                except Exception:
                    db_session.rollback()

    def get_trade_logs(self, trade_id: str) -> list:
        with get_db_session() as db:
            t = db.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
            result = list(t.logs or []) if t else []
            db.close()
            return result

    def get_trade(self, trade_id: str) -> Optional[TradeData]:
        with get_db_session() as db:
            t = db.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
            if not t:
                db.close()
                return None
            result = self._make_trade_data(t)
            db.close()
            return result

    def get_all_trades(self, pair: str) -> List[TradeData]:
        return self.list_trades(pair)

    def clear(self):
        """Delete all trades. Used by scenario runner to reset between runs."""
        with get_db_session() as db:
            db.query(Trade).delete()
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                raise DBException(str(e))
            finally:
                db.close()


# Backward compatibility alias
TradeRepository = ITradeRepository
