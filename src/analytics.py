from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


class AnalyticsReporter(ABC):
    """Injectable interface for crash/event reporting."""

    @abstractmethod
    def capture_exception(self, exc: BaseException, context: Optional[Dict[str, Any]] = None) -> None:
        ...

    @abstractmethod
    def capture_trade_event(self, event_type: str, trade_data: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def capture_signal_event(self, event_type: str, details: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def set_context(self, name: str, data: Dict[str, Any]) -> None:
        ...


class NoOpReporter(AnalyticsReporter):
    def capture_exception(self, exc, context=None):
        pass

    def capture_trade_event(self, event_type, trade_data):
        pass

    def capture_signal_event(self, event_type, details):
        pass

    def set_context(self, name, data):
        pass


class SentryReporter(AnalyticsReporter):
    def __init__(self, dsn: str):
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.0,
            environment="live",
        )

    def capture_exception(self, exc, context=None):
        import sentry_sdk
        if context:
            sentry_sdk.set_context("error_context", context)
        sentry_sdk.capture_exception(exc)

    def capture_trade_event(self, event_type, trade_data):
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category="trade",
            message=event_type,
            data=trade_data,
            level="info",
        )

    def capture_signal_event(self, event_type, details):
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category="strategy",
            message=event_type,
            data=details,
            level="info",
        )

    def set_context(self, name, data):
        import sentry_sdk
        sentry_sdk.set_context(name, data)


# Trade Analytics DTOs
@dataclass
class TradeStatistics:
    """Trade performance statistics."""
    total_trades: int
    open_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float  # R-multiples
    avg_pnl: float    # R-multiples
    avg_win: float    # R-multiples
    avg_loss: float   # R-multiples
    profit_factor: float
    avg_r_multiple: float
    total_pnl_usd: float = 0.0  # Dollar amount
    avg_pnl_usd: float = 0.0    # Dollar amount

    def to_dict(self) -> Dict[str, Any]:
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
class TimeSeriesData:
    """Time series data for charts."""
    labels: List[str]
    values: List[float]

    def to_dict(self) -> Dict[str, List]:
        return {"labels": self.labels, "data": self.values}


@dataclass
class DistributionData:
    """Distribution data for bar/pie charts."""
    labels: List[str]
    values: List[int]

    def to_dict(self) -> Dict[str, List]:
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
    risk_dollars: Optional[float]
    risk_pct: Optional[float]
    contracts: Optional[float]
    entry_time: float  # timestamp
    exit_price: Optional[float]
    exit_time: Optional[float]  # timestamp
    result: Optional[float]
    result_type: Optional[str]
    fees: Optional[float]
    pnl_usd: Optional[float]
    status: str
    logs: List[Dict[str, str]]

    def to_dict(self) -> Dict[str, Any]:
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
