"""Repository interface definitions (Interface Segregation Principle).

This module defines fine-grained repository interfaces following ISP.
Instead of one large repository interface, we have:
- LineReader / LineWriter (for lines)
- TradeReader / TradeWriter (for trades)

This allows code to depend only on the operations it needs.
"""

from abc import ABC, abstractmethod
from typing import Optional, List
from datetime import datetime

from src.models import LineData, TradeData


# =============================================================================
# Line Repository Interfaces
# =============================================================================

class LineReader(ABC):
    """Interface for read-only line operations."""
    
    @abstractmethod
    def get_line(self, line_id: str) -> Optional[LineData]:
        """Get a single line by ID."""
        pass
    
    @abstractmethod
    def list_lines(self, pair: str) -> List[LineData]:
        """List all lines for a pair."""
        pass


class LineWriter(ABC):
    """Interface for write line operations."""
    
    @abstractmethod
    def insert_line(self, pair: str, price: float,
                    creation_date: Optional[datetime] = None) -> LineData:
        """Insert a new line."""
        pass
    
    @abstractmethod
    def update_line(self, line_id: str, price: float) -> LineData:
        """Update a line's price."""
        pass
    
    @abstractmethod
    def delete_line(self, line_id: str) -> None:
        """Delete a line."""
        pass


class LineRepository(LineReader, LineWriter):
    """Full line repository interface (combines read + write)."""
    pass


# =============================================================================
# Trade Repository Interfaces
# =============================================================================

class TradeReader(ABC):
    """Interface for read-only trade operations."""
    
    @abstractmethod
    def get_trade(self, trade_id: str) -> Optional[TradeData]:
        """Get a single trade by ID."""
        pass
    
    @abstractmethod
    def list_trades(self, pair: str) -> List[TradeData]:
        """List all trades for a pair."""
        pass
    
    @abstractmethod
    def get_all_trades(self, pair: str) -> List[TradeData]:
        """Get all trades for a pair (alias for list_trades)."""
        pass


class TradeWriter(ABC):
    """Interface for write trade operations."""
    
    @abstractmethod
    def insert_trade(self, pair: str, trade_type: str, entry_price: float,
                     stop_loss: float, take_profit: float, risk: float,
                     entry_time: datetime, params: Optional[dict] = None,
                     risk_dollars: Optional[float] = None,
                     risk_pct: Optional[float] = None,
                     contracts: Optional[float] = None) -> TradeData:
        """Insert a new trade."""
        pass
    
    @abstractmethod
    def close_trade(self, trade_id: str, exit_price: float, exit_time: datetime,
                   result: float, result_type: Optional[str] = None,
                   fees: Optional[float] = None, pnl_usd: Optional[float] = None) -> TradeData:
        """Close a trade."""
        pass


class TradeModifier(ABC):
    """Interface for modifying existing trades."""
    
    @abstractmethod
    def update_stop_loss(self, trade_id: str, new_stop_loss: float) -> TradeData:
        """Update stop loss."""
        pass
    
    @abstractmethod
    def update_take_profit(self, trade_id: str, new_take_profit: float) -> TradeData:
        """Update take profit."""
        pass
    
    @abstractmethod
    def update_entry_price(self, trade_id: str, new_entry_price: float) -> TradeData:
        """Update entry price."""
        pass
    
    @abstractmethod
    def update_risk_fields(self, trade_id: str, risk: float, risk_dollars: float,
                          risk_pct: float) -> TradeData:
        """Update risk-related fields."""
        pass


class TradeLogger(ABC):
    """Interface for trade logging operations."""
    
    @abstractmethod
    def append_trade_log(self, trade_id: str, event: str, message: str) -> None:
        """Append a log entry to a trade."""
        pass
    
    @abstractmethod
    def get_trade_logs(self, trade_id: str) -> list:
        """Get logs for a trade."""
        pass


class TradeRepository(TradeReader, TradeWriter, TradeModifier, TradeLogger):
    """Full trade repository interface (combines all operations)."""
    pass


# =============================================================================
# Line Trigger State Repository Interfaces
# =============================================================================

class TriggerStateReader(ABC):
    """Interface for read-only trigger state operations."""
    
    @abstractmethod
    def load(self, line_id: str) -> Optional[dict]:
        """Load state for a line."""
        pass
    
    @abstractmethod
    def load_all(self, pair: str) -> dict:
        """Load all states for a pair."""
        pass


class TriggerStateWriter(ABC):
    """Interface for write trigger state operations."""
    
    @abstractmethod
    def save(self, line_id: str, pair: str, state: dict) -> None:
        """Save state for a line."""
        pass
    
    @abstractmethod
    def delete(self, line_id: str) -> None:
        """Delete state for a line."""
        pass


class LineTriggerStateRepository(TriggerStateReader, TriggerStateWriter):
    """Full trigger state repository interface."""
    pass
