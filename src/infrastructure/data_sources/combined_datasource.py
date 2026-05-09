from abc import ABC, abstractmethod
from collections.abc import Callable


class CombinedDataSource(ABC):
    @abstractmethod
    def load_historical_bars(self, timeframe: str = '1m') -> list[dict]:
        """
        Return a list of 1 m bar‐dicts:
          { time, open, high, low, close, volume, pair }
        """
        ...

    @abstractmethod
    def subscribe(self, callback: Callable[[dict], None], from_time: int = 0) -> None:
        """
        Call `callback(msg)` for:
          - each historical bar (as bar‐dict)
          - then each live tick (as tick‐dict: {time, price, volume, pair})
        """
        ...

    @abstractmethod
    def pause(self) -> None:
        """Pause the data source. Implementations that block should break out."""
        ...
