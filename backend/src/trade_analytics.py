"""DTOs for trade analytics data."""
from dataclasses import dataclass
from typing import Any


@dataclass
class TradeStatistics:
    """Trade performance statistics."""
    total_trades: int
    open_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_pnl_usd: float
    avg_pnl: float
    avg_pnl_usd: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    avg_r_multiple: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "open_trades": self.open_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 2),
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "avg_pnl": round(self.avg_pnl, 2),
            "avg_pnl_usd": round(self.avg_pnl_usd, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "profit_factor": round(self.profit_factor, 2) if self.profit_factor != float('inf') else None,
            "avg_r_multiple": round(self.avg_r_multiple, 2),
        }


@dataclass
class TimeSeriesPoint:
    """Single point in a time series."""
    label: str
    value: float


@dataclass
class TimeSeriesData:
    """Time series data for charts."""
    labels: list[str]
    values: list[float]

    def to_dict(self) -> dict[str, list]:
        return {"labels": self.labels, "data": self.values}


@dataclass
class DistributionData:
    """Distribution data for bar/pie charts."""
    labels: list[str]
    values: list[int]

    def to_dict(self) -> dict[str, list]:
        return {"labels": self.labels, "data": self.values}


@dataclass
class TradeDetail:
    """Full trade details including logs."""
    trade_id: str
    pair: str
    trade_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk: float
    risk_dollars: float | None
    risk_pct: float | None
    account_balance: float | None
    contracts: float | None
    entry_time: float  # timestamp
    exit_price: float | None
    exit_time: float | None  # timestamp
    result: float | None
    result_type: str | None
    fees: float | None
    pnl_usd: float | None
    status: str
    logs: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "pair": self.pair,
            "type": self.trade_type,
            "entry": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk": self.risk,
            "risk_dollars": self.risk_dollars,
            "risk_pct": self.risk_pct,
            "account_balance": self.account_balance,
            "contracts": self.contracts,
            "entry_time": self.entry_time,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time,
            "result": self.result,
            "result_type": self.result_type,
            "fees": self.fees,
            "pnl_usd": self.pnl_usd,
            "status": self.status,
            "logs": self.logs,
        }
