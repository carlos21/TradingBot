from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any


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
    entry_time: datetime
    exit_price: Optional[float]
    exit_time: Optional[datetime]
    result: Optional[float]       # e.g. PnL or +1/–1 flag
    params: Optional[Dict[str, Any]]
    created_at: datetime

    def __hash__(self):
        return hash((
            self.trade_id,
            self.pair,
            self.trade_type,
            self.entry_price,
            self.stop_loss,
            self.take_profit,
            self.risk,
            self.entry_time,
            self.exit_price,
            self.exit_time,
            self.result,
            # Note: you can choose whether to include params in the hash
            self.created_at
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
            self.entry_time   == other.entry_time and
            self.exit_price   == other.exit_price and
            self.exit_time    == other.exit_time and
            self.result       == other.result and
            self.params       == other.params and
            self.created_at   == other.created_at
        )