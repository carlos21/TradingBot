from abc import ABC, abstractmethod
from typing import Callable, Dict, List

class CombinedDataSource(ABC):
    @abstractmethod
    def load_historical_bars(self) -> List[Dict]:
        """
        Return a list of 1 m bar‐dicts:
          { time, open, high, low, close, volume, pair }
        """
        ...

    @abstractmethod
    def subscribe(self, callback: Callable[[Dict], None]) -> None:
        """
        Call `callback(msg)` for:
          - each historical bar (as bar‐dict)
          - then each live tick (as tick‐dict: {time, price, volume, pair})
        """
        ...