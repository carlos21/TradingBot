"""Repository interface definitions (Interface Segregation Principle).

This module defines fine-grained repository interfaces following ISP.
Instead of one large repository interface, we have:
- LineReader / LineWriter (for lines)
- TradeReader / TradeWriter (for trades)

This allows code to depend only on the operations it needs.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from src.models import LineData, TradeData

# =============================================================================
# Line Repository Interfaces
# =============================================================================

class LineReader(ABC):
    """Interface for read-only line operations."""

    @abstractmethod
    def get_line(self, line_id: str) -> LineData | None:
        """Get a single line by ID."""

    @abstractmethod
    def list_lines(self, pair: str) -> list[LineData]:
        """List all lines for a pair."""


class LineWriter(ABC):
    """Interface for write line operations."""

    @abstractmethod
    def insert_line(self, pair: str, price: float,
                    creation_date: datetime | None = None) -> LineData:
        """Insert a new line."""

    @abstractmethod
    def update_line(self, line_id: str, price: float) -> LineData:
        """Update a line's price."""

    @abstractmethod
    def delete_line(self, line_id: str) -> None:
        """Delete a line."""


class LineRepository(LineReader, LineWriter):
    """Full line repository interface (combines read + write)."""


# =============================================================================
# Trade Repository Interfaces
# =============================================================================

class TradeReader(ABC):
    """Interface for read-only trade operations."""

    @abstractmethod
    def get_trade(self, trade_id: str) -> TradeData | None:
        """Get a single trade by ID."""

    @abstractmethod
    def list_trades(self, pair: str) -> list[TradeData]:
        """List all trades for a pair."""

    @abstractmethod
    def get_all_trades(self, pair: str) -> list[TradeData]:
        """Get all trades for a pair (alias for list_trades)."""


class TradeWriter(ABC):
    """Interface for write trade operations."""

    @abstractmethod
    def insert_trade(self, pair: str, trade_type: str, entry_price: float,
                     stop_loss: float, take_profit: float, risk: float,
                     entry_time: datetime, params: dict | None = None,
                     risk_dollars: float | None = None,
                     risk_pct: float | None = None,
                     contracts: float | None = None,
                     trade_id: str | None = None) -> TradeData:
        """Insert a new trade. If trade_id is provided, use it instead of generating a UUID."""

    @abstractmethod
    def close_trade(self, trade_id: str, exit_price: float, exit_time: datetime,
                   result: float, result_type: str | None = None,
                   fees: float | None = None, pnl_usd: float | None = None) -> TradeData:
        """Close a trade."""


class TradeModifier(ABC):
    """Interface for modifying existing trades."""

    @abstractmethod
    def update_stop_loss(self, trade_id: str, new_stop_loss: float) -> TradeData:
        """Update stop loss."""

    @abstractmethod
    def update_take_profit(self, trade_id: str, new_take_profit: float) -> TradeData:
        """Update take profit."""

    @abstractmethod
    def update_entry_price(self, trade_id: str, new_entry_price: float) -> TradeData:
        """Update entry price."""

    @abstractmethod
    def update_risk_fields(self, trade_id: str, risk: float, risk_dollars: float,
                          risk_pct: float) -> TradeData:
        """Update risk-related fields."""

    @abstractmethod
    def update_contracts(self, trade_id: str, contracts: float) -> TradeData:
        """Update the contracts/lots field for a trade."""


class TradeLogger(ABC):
    """Interface for trade logging operations."""

    @abstractmethod
    def append_trade_log(self, trade_id: str, event: str, message: str) -> None:
        """Append a log entry to a trade."""

    @abstractmethod
    def get_trade_logs(self, trade_id: str) -> list:
        """Get logs for a trade."""


class TradeRepository(TradeReader, TradeWriter, TradeModifier, TradeLogger):
    """Full trade repository interface (combines all operations)."""

    def clear_in_memory(self) -> None:
        """Clear in-memory state. No-op for persistent repositories."""


# =============================================================================
# Line Trigger State Repository Interfaces
# =============================================================================

class TriggerStateReader(ABC):
    """Interface for read-only trigger state operations."""

    @abstractmethod
    def load(self, line_id: str) -> dict | None:
        """Load state for a line."""

    @abstractmethod
    def load_all(self, pair: str) -> dict:
        """Load all states for a pair."""


class TriggerStateWriter(ABC):
    """Interface for write trigger state operations."""

    @abstractmethod
    def save(self, line_id: str, pair: str, state: dict) -> None:
        """Save state for a line."""

    @abstractmethod
    def delete(self, line_id: str) -> None:
        """Delete state for a line."""


class LineTriggerStateRepository(TriggerStateReader, TriggerStateWriter):
    """Full trigger state repository interface."""
