from abc import ABC, abstractmethod


class TradeExecutor(ABC):
    @abstractmethod
    def on_trade_open(self, trade: dict) -> None: ...

    @abstractmethod
    def on_trade_close(self, trade_id: str, exit_price: float) -> None: ...

    @abstractmethod
    def on_sl_update(self, trade_id: str, new_sl: float) -> None: ...


class NoOpExecutor(TradeExecutor):
    """Used in backtest mode — does nothing."""
    def on_trade_open(self, trade): pass
    def on_trade_close(self, trade_id, exit_price): pass
    def on_sl_update(self, trade_id, new_sl): pass


class NinjaTraderExecutor(TradeExecutor):
    """Sends trade commands to NinjaTrader via the data source command queue."""
    def __init__(self, data_source, *, risk_usd: float = None, risk_pct: float = None):
        self._ds = data_source
        self._risk_usd = risk_usd
        self._risk_pct = risk_pct

    def on_trade_open(self, trade):
        cmd = {
            "command": "place_order",
            "trade_id": trade["trade_id"],
            "pair": trade["pair"],
            "direction": trade["type"],
            "entry_price": trade["entry"],
            "sl_points": trade["risk"],
            "rr_ratio": trade.get("rr_ratio", 5.0),
        }
        if self._risk_usd is not None:
            cmd["risk_usd"] = self._risk_usd
        elif self._risk_pct is not None:
            cmd["risk_pct"] = self._risk_pct
        self._ds.enqueue_command(cmd)

    def on_trade_close(self, trade_id, exit_price):
        self._ds.enqueue_command({
            "command": "close_order",
            "trade_id": trade_id,
        })

    def on_sl_update(self, trade_id, new_sl):
        self._ds.enqueue_command({
            "command": "modify_order",
            "trade_id": trade_id,
            "stop_loss": new_sl,
        })
