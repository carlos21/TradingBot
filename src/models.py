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
    contracts: float | None
    entry_time: datetime
    exit_price: float | None
    exit_time: datetime | None
    result: float | None       # e.g. PnL or +1/–1 flag
    result_type: str | None    # "TP", "SL", "BE", or "SP"
    fees: float | None
    pnl_usd: float | None
    params: dict[str, Any] | None
    logs: list[dict[str, str]] | None = field(default=None)
    source: str | None = field(default=None)
    created_at: datetime = field(default=None)

    def __hash__(self):
        return hash((
            self.trade_id,
            self.pair,
            self.trade_type,
            self.entry_price,
            self.stop_loss,
            self.take_profit,
            self.risk,
            self.risk_dollars,
            self.risk_pct,
            self.contracts,
            self.entry_time,
            self.exit_price,
            self.exit_time,
            self.result,
            self.result_type,
            self.fees,
            self.pnl_usd,
            self.params,
            self.source,
            self.created_at,
        ))

    def __eq__(self, other):
        if not isinstance(other, TradeData):
            return NotImplemented
        return (
            self.trade_id     == other.trade_id and
            self.pair         == other.pair and
            self.trade_type   == other.trade_type and
            self.entry_price  == other.entry_price and
            self.stop_loss    == other.stop_loss and
            self.take_profit  == other.take_profit and
            self.risk         == other.risk and
            self.risk_dollars == other.risk_dollars and
            self.risk_pct     == other.risk_pct and
            self.contracts    == other.contracts and
            self.entry_time   == other.entry_time and
            self.exit_price   == other.exit_price and
            self.exit_time    == other.exit_time and
            self.result       == other.result and
            self.result_type  == other.result_type and
            self.fees         == other.fees and
            self.pnl_usd     == other.pnl_usd and
            self.params       == other.params and
            self.source       == other.source and
            self.created_at   == other.created_at
        )
