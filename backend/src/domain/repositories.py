"""Repository interface definitions (Interface Segregation Principle).

This module defines fine-grained repository interfaces following ISP.
Instead of one large repository interface, we have:
- LineReader / LineWriter (for lines)
- TradeReader / TradeWriter (for trades)

This allows code to depend only on the operations it needs.

All repository interfaces live in the domain layer so that:
- Domain code depends on abstractions, not implementations
- Infrastructure implements these interfaces
- The dependency arrow points inward (domain knows nothing of infra)
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol

from src.domain.models import Instrument, LineData, PnLResult, TradeData

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
                     account_balance: float | None = None,
                     contracts: float | None = None,
                     source: str | None = None,
                     account: str | None = None,
                     signal_id: str | None = None,
                     trade_id: str | None = None) -> TradeData:
        """Insert a new trade. If trade_id is provided, use it instead of generating a UUID."""

    @abstractmethod
    def close_trade(self, trade_id: str, exit_price: float, exit_time: datetime,
                   result: float, result_type: str | None = None,
                   fees: float | None = None, pnl_usd: float | None = None,
                   gross_pnl: float | None = None,
                   realized_pnl: float | None = None) -> TradeData:
        """Close a trade."""

    @abstractmethod
    def delete_trade(self, trade_id: str) -> None:
        """Delete a trade and any related child trades."""

    @abstractmethod
    def delete_trades(self, trade_ids: list[str]) -> None:
        """Delete multiple trades and their related child trades atomically."""


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

    @abstractmethod
    def update_account_balance(self, trade_id: str, account_balance: float) -> TradeData:
        """Update the account balance snapshot for a trade."""


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


# =============================================================================
# Account Repository Interface
# =============================================================================

class AccountConfig:
    """Value object for account configuration."""

    def __init__(self, name: str, risk_usd: float | None = None,
                 risk_pct: float | None = None, rr_ratio: float | None = None,
                 live_enabled: bool = True, instrument_symbols: list[str] | None = None):
        self.name = name
        self.risk_usd = risk_usd
        self.risk_pct = risk_pct
        self.rr_ratio = rr_ratio
        self.live_enabled = live_enabled
        self.instrument_symbols = list(instrument_symbols) if instrument_symbols else []


class AccountReader(ABC):
    """Interface for read-only account operations."""

    @abstractmethod
    def list_accounts(self) -> list[AccountConfig]:
        """List all configured accounts."""

    @abstractmethod
    def get_account(self, name: str) -> AccountConfig | None:
        """Get a single account by name."""


class AccountWriter(ABC):
    """Interface for write account operations."""

    @abstractmethod
    def upsert(self, name: str, risk_usd: float | None = None,
               risk_pct: float | None = None, rr_ratio: float | None = None,
               live_enabled: bool = True,
               instrument_symbols: list[str] | None = None) -> None:
        """Save or update an account configuration."""

    @abstractmethod
    def delete(self, name: str) -> None:
        """Delete an account configuration."""

    @abstractmethod
    def clear_all(self) -> None:
        """Delete all account configurations."""


class AccountRepository(AccountReader, AccountWriter):
    """Full account repository interface."""


# =============================================================================
# Credential Repository Interface
# =============================================================================

class CredentialReader(ABC):
    """Interface for read-only credential operations."""

    @abstractmethod
    def get_credential(self, service: str) -> tuple[str, str] | None:
        """Get a credential by service name. Returns (username, password_encrypted)."""

    @abstractmethod
    def list_all(self) -> list[dict]:
        """List all credentials (without passwords)."""


class CredentialWriter(ABC):
    """Interface for write credential operations."""

    @abstractmethod
    def save_credential(self, service: str, username: str, password_encrypted: str) -> None:
        """Save or update a credential."""

    @abstractmethod
    def delete_credential(self, service: str) -> None:
        """Delete a credential by service name."""


class CredentialRepository(CredentialReader, CredentialWriter):
    """Full credential repository interface."""


# =============================================================================
# Decision Log Repository Interface
# =============================================================================

class DecisionLogReader(ABC):
    """Interface for read-only decision log operations."""

    @abstractmethod
    def get_recent(self, pair: str | None = None, event: str | None = None,
                   line_id: str | None = None, limit: int = 500) -> list[dict]:
        """Query recent decision logs with optional filters."""

    @abstractmethod
    def get_by_line_id(self, line_id: str) -> list[dict]:
        """Full audit trail for a single strategy line."""


class DecisionLogWriter(ABC):
    """Interface for write decision log operations."""

    @abstractmethod
    def add_log(self, *, bar_time: float, pair: str, tf: str | None = None,
                line_id: str | None = None, event: str,
                direction: str | None = None, trigger_name: str | None = None,
                filter_name: str | None = None, reason: str | None = None,
                details: str | None = None) -> None:
        """Insert a single decision log."""

    @abstractmethod
    def cleanup_old(self, days: int = 30) -> int:
        """Delete logs older than N days. Returns number of rows deleted."""


class DecisionLogRepository(DecisionLogReader, DecisionLogWriter):
    """Full decision log repository interface."""


# =============================================================================
# Instrument Registry Interface
# =============================================================================

class InstrumentCatalog(Protocol):
    """The supported instruments and their factory defaults.

    Implemented by the hardcoded catalog in the strategies layer; injected
    into the registry so the supported set is a composition decision, not a
    global.
    """

    def get_defaults(self) -> list[Instrument]:
        """Return every supported instrument with its default values."""


class IInstrumentRegistry(Protocol):
    """Protocol for loading and persisting the instrument registry."""

    def get_all(self) -> list[Instrument]:
        """Return the registered instruments, defaulting to one if empty."""

    def save(self, instruments: list[Instrument]) -> None:
        """Persist the instrument registry."""


# =============================================================================
# Settings Repository Interface
# =============================================================================

class SettingsReader(ABC):
    """Interface for read-only settings operations."""

    @abstractmethod
    def get(self, key: str) -> str | None:
        """Get a setting value by key."""

    @abstractmethod
    def get_all(self) -> dict[str, str]:
        """Get all settings as a dictionary."""


class SettingsWriter(ABC):
    """Interface for write settings operations."""

    @abstractmethod
    def set(self, key: str, value: str, is_sensitive: bool = False) -> None:
        """Set a setting value."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete a setting."""


class SettingsRepository(SettingsReader, SettingsWriter):
    """Full settings repository interface."""
