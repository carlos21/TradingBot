from abc import ABC, abstractmethod
from collections.abc import Callable


class LiveDataSource(ABC):
    """Anything that can push you live ticks."""
    @abstractmethod
    def subscribe(self, callback: Callable[[dict], None]) -> None:
        """
        Register `callback(tick_dict)` to be called on every new tick.
        tick_dict must contain at least:
            { 'time': int, 'price': float, 'volume': int, 'pair': str }
        """

    @abstractmethod
    def load_historical_ticks(self) -> list[dict]:
        """
        Return the full history as a list of ticks:
        {time, price, volume, pair}...
        """
