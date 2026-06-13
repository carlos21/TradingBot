"""Fake order tracker that mirrors the C# NinjaTrader OrderTracker behaviour.

Tracks orders per account using the same naming conventions
(`Entry_{trade_id}`, `Stop_{trade_id}`, `Target_{trade_id}`) so that
crash-recovery sync and command routing behave identically to real NT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class _TrackedOrder:
    trade_id: str
    account: str
    direction: Literal["long", "short"]
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float
    status: Literal["pending", "filled", "cancelled", "closed"] = "pending"


class FakeOrderTracker:
    """In-memory order tracker for the fake NinjaTrader."""

    def __init__(self, accounts: list[str]) -> None:
        self._accounts = set(accounts)
        # trade_id -> _TrackedOrder
        self._entries: dict[str, _TrackedOrder] = {}
        self._stops: dict[str, _TrackedOrder] = {}
        self._targets: dict[str, _TrackedOrder] = {}
        # seq_num -> True  (for duplicate command detection)
        self._processed_seq_nums: set[int] = set()

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def is_duplicate(self, seq_num: int) -> bool:
        return seq_num in self._processed_seq_nums

    def mark_processed(self, seq_num: int) -> None:
        self._processed_seq_nums.add(seq_num)

    # ------------------------------------------------------------------
    # Entry orders
    # ------------------------------------------------------------------

    def track_entry(
        self,
        trade_id: str,
        account: str | None,
        direction: Literal["long", "short"],
        quantity: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> tuple[bool, str]:
        """Track a new entry order. Returns (ok, reason)."""
        resolved_account = self._resolve_account(account)
        if resolved_account is None:
            return False, f"No account available (requested: {account or '(default)'} )"

        if trade_id in self._entries:
            return False, f"Entry order already tracked for {trade_id}"

        self._entries[trade_id] = _TrackedOrder(
            trade_id=trade_id,
            account=resolved_account,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        return True, ""

    def fill_entry(self, trade_id: str) -> _TrackedOrder | None:
        """Mark an entry order as filled and auto-create SL/TP bracket orders."""
        entry = self._entries.get(trade_id)
        if entry is None:
            return None
        if entry.status != "pending":
            return None

        entry.status = "filled"
        # Create bracket orders
        self._stops[trade_id] = _TrackedOrder(
            trade_id=trade_id,
            account=entry.account,
            direction=entry.direction,
            quantity=entry.quantity,
            entry_price=entry.stop_loss,
            stop_loss=entry.stop_loss,
            take_profit=entry.take_profit,
            status="pending",
        )
        self._targets[trade_id] = _TrackedOrder(
            trade_id=trade_id,
            account=entry.account,
            direction=entry.direction,
            quantity=entry.quantity,
            entry_price=entry.take_profit,
            stop_loss=entry.stop_loss,
            take_profit=entry.take_profit,
            status="pending",
        )
        return entry

    # ------------------------------------------------------------------
    # Close orders
    # ------------------------------------------------------------------

    def track_close(self, trade_id: str, account: str | None) -> tuple[bool, str, _TrackedOrder | None]:
        """Close a position. Returns (ok, reason, entry_order)."""
        resolved_account = self._resolve_account(account)
        if resolved_account is None:
            return False, f"No account available (requested: {account or '(default)'} )", None

        entry = self._entries.get(trade_id)
        if entry is None:
            return False, "Trade not tracked — already closed or never opened", None

        if entry.account != resolved_account:
            return False, f"Account mismatch for {trade_id}", None

        # Cancel working stops/targets
        stop = self._stops.pop(trade_id, None)
        target = self._targets.pop(trade_id, None)
        if stop:
            stop.status = "cancelled"
        if target:
            target.status = "cancelled"

        entry.status = "closed"
        return True, "", entry

    # ------------------------------------------------------------------
    # Modify orders (Cancel + Replace)
    # ------------------------------------------------------------------

    def track_modify(
        self,
        trade_id: str,
        account: str | None,
        new_stop_loss: float,
    ) -> tuple[bool, str, _TrackedOrder | None]:
        """Modify stop loss via cancel+replace. Returns (ok, reason, entry_order)."""
        resolved_account = self._resolve_account(account)
        if resolved_account is None:
            return False, f"No account available (requested: {account or '(default)'} )", None

        entry = self._entries.get(trade_id)
        if entry is None:
            return False, f"Stop order not found for trade {trade_id}", None

        if entry.account != resolved_account:
            return False, f"Account mismatch for {trade_id}", None

        stop = self._stops.get(trade_id)
        if stop is None:
            return False, f"Stop order not found for trade {trade_id}", None

        if stop.status not in ("pending",):
            return False, f"Stop order not modifiable (status={stop.status})", None

        # Cancel old stop + create replacement
        stop.status = "cancelled"
        self._stops[trade_id] = _TrackedOrder(
            trade_id=trade_id,
            account=entry.account,
            direction=entry.direction,
            quantity=entry.quantity,
            entry_price=new_stop_loss,
            stop_loss=new_stop_loss,
            take_profit=entry.take_profit,
            status="pending",
        )
        entry.stop_loss = new_stop_loss
        return True, "", entry

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_position(self, trade_id: str) -> dict | None:
        """Return a broker-style position dict for a trade, or None."""
        entry = self._entries.get(trade_id)
        if entry is None or entry.status != "filled":
            return None
        return {
            "trade_id": trade_id,
            "direction": entry.direction,
            "entry_price": entry.entry_price,
            "stop_loss": entry.stop_loss,
            "take_profit": entry.take_profit,
            "quantity": entry.quantity,
            "account": entry.account,
        }

    def all_positions(self) -> list[dict]:
        """Return all open (filled) positions for POSITION_SYNC."""
        return [
            {
                "trade_id": t.trade_id,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "quantity": t.quantity,
                "account": t.account,
            }
            for t in self._entries.values()
            if t.status == "filled"
        ]

    def get_entry(self, trade_id: str) -> _TrackedOrder | None:
        return self._entries.get(trade_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_account(self, account_name: str | None) -> str | None:
        if account_name and account_name in self._accounts:
            return account_name
        if account_name is None or account_name == "":
            if len(self._accounts) == 1:
                return next(iter(self._accounts))
            return None
        return None
