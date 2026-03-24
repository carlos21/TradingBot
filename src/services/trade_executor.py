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
    def __init__(self, data_source):
        self._ds = data_source

    def on_trade_open(self, trade):
        self._ds.enqueue_command({
            "command": "place_order",
            "trade_id": trade["trade_id"],
            "pair": trade["pair"],
            "direction": trade["type"],
            "entry_price": trade["entry"],
            "stop_loss": trade["stop_loss"],
            "take_profit": trade["take_profit"],
        })

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
