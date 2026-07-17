from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class LineData:
    line_id: str
    pair: str
    price: float
    creation_date: datetime

    def __hash__(self):
        return hash((self.line_id, self.pair, self.price, self.creation_date))

    def __eq__(self, other):
        if not isinstance(other, LineData):
            return NotImplemented
        return (
            self.line_id == other.line_id and
            self.pair == other.pair and
            self.price == other.price and
            self.creation_date == other.creation_date
        )


@dataclass
class Instrument:
    """A tradeable instrument configuration."""

    symbol: str
    full_name: str
    point_value: float = 2.0


@dataclass
class TradeData:
    trade_id: str
    pair: str
    trade_type: str        # "long" or "short"
    entry_price: float
    stop_loss: float
    take_profit: float
    risk: float
    risk_dollars: float | None
    risk_pct: float | None
    account_balance: float | None
    contracts: float | None
    entry_time: datetime
    exit_price: float | None
    exit_time: datetime | None
    result: float | None       # e.g. PnL or +1/–1 flag
    result_type: str | None    # "TP", "SL", "BE", "SP", or "CLOSE" (null while open)
    fees: float | None
    pnl_usd: float | None
    params: dict[str, Any] | None
    logs: list[dict[str, str]] | None = field(default=None)
    source: str | None = field(default=None)
    account: str | None = field(default=None)
    signal_id: str | None = field(default=None)
    created_at: datetime = field(default=None)

    def __hash__(self):
        # Hash only on immutable, hashable fields.  params/logs are dict/list
        # and must not participate in hashing.
        return hash(self.trade_id)

    def __eq__(self, other):
        if not isinstance(other, TradeData):
            return NotImplemented
        return self.trade_id == other.trade_id
