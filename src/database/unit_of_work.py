"""Unit of Work pattern for database transactions.

The Unit of Work pattern manages transactions across multiple repository operations,
ensuring atomic commits and rollbacks. This eliminates duplicate try/except/rollback
boilerplate in repository implementations.

Usage:
    with UnitOfWork() as uow:
        line = uow.lines.insert_line(pair="MNQ", price=5000.0)
        trade = uow.trades.insert_trade(pair="MNQ", ...)
        # Both operations committed together on __exit__

    # Or manual control:
    uow = UnitOfWork()
    try:
        line = uow.lines.insert_line(pair="MNQ", price=5000.0)
        uow.commit()
    except:
        uow.rollback()
        raise
    finally:
        uow.close()
"""

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, TypeVar

from src.database.database import Line, LineTriggerState, Trade
from src.dbexception import DBNotFoundException
from src.models import LineData, TradeData

T = TypeVar('T')


class ILineRepository(ABC):
    """Interface for line repository operations within a Unit of Work."""

    @abstractmethod
    def insert_line(self, pair: str, price: float, creation_date: datetime | None = None) -> LineData:
        pass

    @abstractmethod
    def get_line(self, line_id: str) -> LineData | None:
        pass

    @abstractmethod
    def list_lines(self, pair: str) -> list[LineData]:
        pass

    @abstractmethod
    def update_line(self, line_id: str, price: float) -> LineData:
        pass

    @abstractmethod
    def delete_line(self, line_id: str) -> None:
        pass


class ITradeRepository(ABC):
    """Interface for trade repository operations within a Unit of Work."""

    @abstractmethod
    def insert_trade(self, pair: str, trade_type: str, entry_price: float,
                     stop_loss: float, take_profit: float, risk: float,
                     entry_time: datetime, params: dict | None = None,
                     risk_dollars: float | None = None,
                     risk_pct: float | None = None,
                     contracts: float | None = None) -> TradeData:
        pass

    @abstractmethod
    def list_trades(self, pair: str) -> list[TradeData]:
        pass

    @abstractmethod
    def update_stop_loss(self, trade_id: str, new_stop_loss: float) -> TradeData:
        pass

    @abstractmethod
    def update_take_profit(self, trade_id: str, new_take_profit: float) -> TradeData:
        pass

    @abstractmethod
    def update_entry_price(self, trade_id: str, new_entry_price: float) -> TradeData:
        pass

    @abstractmethod
    def update_risk_fields(self, trade_id: str, risk: float, risk_dollars: float,
                          risk_pct: float) -> TradeData:
        pass

    @abstractmethod
    def close_trade(self, trade_id: str, exit_price: float, exit_time: datetime,
                   result: float, result_type: str | None = None,
                   fees: float | None = None, pnl_usd: float | None = None) -> TradeData:
        pass

    @abstractmethod
    def get_trade(self, trade_id: str) -> TradeData | None:
        pass

    @abstractmethod
    def get_all_trades(self, pair: str) -> list[TradeData]:
        pass


class ILineTriggerStateRepository(ABC):
    """Interface for line trigger state repository operations."""

    @abstractmethod
    def save(self, line_id: str, pair: str, state: dict[str, Any]) -> None:
        pass

    @abstractmethod
    def load(self, line_id: str) -> dict[str, Any] | None:
        pass

    @abstractmethod
    def load_all(self, pair: str) -> dict[str, dict[str, Any]]:
        pass

    @abstractmethod
    def delete(self, line_id: str) -> None:
        pass


class UnitOfWorkLineRepository(ILineRepository):
    """Line repository that operates within a Unit of Work session."""

    def __init__(self, session):
        self._session = session

    def _ensure_utc_aware(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _make_line_data(self, line: Line) -> LineData:
        return LineData(
            line_id=line.line_id,
            pair=line.pair,
            price=line.price,
            creation_date=self._ensure_utc_aware(line.creation_date),
        )

    def insert_line(self, pair: str, price: float, creation_date: datetime | None = None) -> LineData:
        c_date = creation_date if creation_date else datetime.now(timezone.utc)
        new_line = Line(
            line_id=str(uuid.uuid4()),
            pair=pair,
            price=price,
            creation_date=c_date
        )
        self._session.add(new_line)
        self._session.flush()  # Flush to get the ID without committing
        return self._make_line_data(new_line)

    def get_line(self, line_id: str) -> LineData | None:
        row = self._session.query(Line).filter(Line.line_id == line_id).one_or_none()
        if not row:
            return None
        return self._make_line_data(row)

    def list_lines(self, pair: str) -> list[LineData]:
        rows = self._session.query(Line).filter(Line.pair == pair).all()
        return [self._make_line_data(row) for row in rows]

    def update_line(self, line_id: str, price: float) -> LineData:
        row = self._session.query(Line).filter(Line.line_id == line_id).one_or_none()
        if not row:
            raise DBNotFoundException(f"Line {line_id} not found")
        row.price = price
        self._session.flush()
        return self._make_line_data(row)

    def delete_line(self, line_id: str) -> None:
        self._session.query(Line).filter(Line.line_id == line_id).delete()


class UnitOfWorkTradeRepository(ITradeRepository):
    """Trade repository that operates within a Unit of Work session."""

    def __init__(self, session):
        self._session = session

    def _ensure_utc(self, dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _make_trade_data(self, t: Trade) -> TradeData:
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

    def insert_trade(self, pair: str, trade_type: str, entry_price: float,
                     stop_loss: float, take_profit: float, risk: float,
                     entry_time: datetime, params: dict | None = None,
                     risk_dollars: float | None = None,
                     risk_pct: float | None = None,
                     contracts: float | None = None) -> TradeData:
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
        self._session.add(t)
        self._session.flush()
        return self._make_trade_data(t)

    def list_trades(self, pair: str) -> list[TradeData]:
        rows = self._session.query(Trade).filter(Trade.pair == pair).all()
        return [self._make_trade_data(t) for t in rows]

    def update_stop_loss(self, trade_id: str, new_stop_loss: float) -> TradeData:
        t = self._session.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
        if not t:
            raise DBNotFoundException(f"Trade {trade_id} not found")
        t.stop_loss = new_stop_loss
        self._session.flush()
        return self._make_trade_data(t)

    def update_take_profit(self, trade_id: str, new_take_profit: float) -> TradeData:
        t = self._session.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
        if not t:
            raise DBNotFoundException(f"Trade {trade_id} not found")
        t.take_profit = new_take_profit
        self._session.flush()
        return self._make_trade_data(t)

    def update_entry_price(self, trade_id: str, new_entry_price: float) -> TradeData:
        t = self._session.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
        if not t:
            raise DBNotFoundException(f"Trade {trade_id} not found")
        t.entry_price = new_entry_price
        self._session.flush()
        return self._make_trade_data(t)

    def update_risk_fields(self, trade_id: str, risk: float, risk_dollars: float,
                          risk_pct: float) -> TradeData:
        t = self._session.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
        if not t:
            raise DBNotFoundException(f"Trade {trade_id} not found")
        t.risk = risk
        t.risk_dollars = risk_dollars
        t.risk_pct = risk_pct
        self._session.flush()
        return self._make_trade_data(t)

    def close_trade(self, trade_id: str, exit_price: float, exit_time: datetime,
                   result: float, result_type: str | None = None,
                   fees: float | None = None, pnl_usd: float | None = None) -> TradeData:
        t = self._session.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
        if not t:
            raise DBNotFoundException(f"Trade {trade_id} not found")
        t.exit_price = exit_price
        t.exit_time = exit_time
        t.result = result
        t.result_type = result_type
        t.fees = fees
        t.pnl_usd = pnl_usd
        self._session.flush()
        return self._make_trade_data(t)

    def get_trade(self, trade_id: str) -> TradeData | None:
        t = self._session.query(Trade).filter(Trade.trade_id == trade_id).one_or_none()
        if not t:
            return None
        return self._make_trade_data(t)

    def get_all_trades(self, pair: str) -> list[TradeData]:
        return self.list_trades(pair)


class UnitOfWorkLineTriggerStateRepository(ILineTriggerStateRepository):
    """Line trigger state repository that operates within a Unit of Work session."""

    def __init__(self, session):
        self._session = session

    def save(self, line_id: str, pair: str, state: dict[str, Any]) -> None:
        from sqlalchemy.dialects.sqlite import insert as sqlite_upsert

        stmt = sqlite_upsert(LineTriggerState).values(
            line_id=line_id,
            pair=pair,
            state_json=state,
            updated_at=datetime.now(timezone.utc)
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=['line_id'],
            set_={'state_json': state, 'updated_at': datetime.now(timezone.utc)}
        )
        self._session.execute(stmt)

    def load(self, line_id: str) -> dict[str, Any] | None:
        row = self._session.query(LineTriggerState).filter(
            LineTriggerState.line_id == line_id
        ).one_or_none()
        if row:
            return dict(row.state_json)
        return None

    def load_all(self, pair: str) -> dict[str, dict[str, Any]]:
        rows = self._session.query(LineTriggerState).filter(
            LineTriggerState.pair == pair
        ).all()
        return {row.line_id: dict(row.state_json) for row in rows}

    def delete(self, line_id: str) -> None:
        self._session.query(LineTriggerState).filter(
            LineTriggerState.line_id == line_id
        ).delete()


class UnitOfWork:
    """Unit of Work for managing database transactions.

    This class manages a single database session and coordinates
    commits/rollbacks across multiple repository operations.

    Usage:
        # Context manager (recommended)
        with UnitOfWork() as uow:
            line = uow.lines.insert_line(pair="MNQ", price=5000.0)
            trade = uow.trades.insert_trade(pair="MNQ", ...)
            # Auto-commits on success, rolls back on exception

        # Manual control
        uow = UnitOfWork()
        try:
            line = uow.lines.insert_line(...)
            uow.commit()
        except:
            uow.rollback()
            raise
        finally:
            uow.close()
    """

    def __init__(self):
        self._session = None
        self._lines: UnitOfWorkLineRepository | None = None
        self._trades: UnitOfWorkTradeRepository | None = None
        self._trigger_state: UnitOfWorkLineTriggerStateRepository | None = None

    def _init_session(self):
        """Initialize the database session if not already done."""
        if self._session is None:
            from src.database.database import db
            self._session = db.get_session()

    @property
    def lines(self) -> ILineRepository:
        """Get the lines repository."""
        self._init_session()
        if self._lines is None:
            self._lines = UnitOfWorkLineRepository(self._session)
        return self._lines

    @property
    def trades(self) -> ITradeRepository:
        """Get the trades repository."""
        self._init_session()
        if self._trades is None:
            self._trades = UnitOfWorkTradeRepository(self._session)
        return self._trades

    @property
    def trigger_state(self) -> ILineTriggerStateRepository:
        """Get the trigger state repository."""
        self._init_session()
        if self._trigger_state is None:
            self._trigger_state = UnitOfWorkLineTriggerStateRepository(self._session)
        return self._trigger_state

    def commit(self) -> None:
        """Commit the current transaction."""
        if self._session:
            self._session.commit()

    def rollback(self) -> None:
        """Rollback the current transaction."""
        if self._session:
            self._session.rollback()

    def close(self) -> None:
        """Close the session."""
        if self._session:
            self._session.close()
            self._session = None
            self._lines = None
            self._trades = None
            self._trigger_state = None

    def __enter__(self):
        """Enter context manager."""
        self._init_session()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager - auto-commit or rollback."""
        if exc_type is None:
            # No exception - commit
            self.commit()
        else:
            # Exception occurred - rollback
            self.rollback()
        self.close()
        # Don't suppress the exception
        return False
