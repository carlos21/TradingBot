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



