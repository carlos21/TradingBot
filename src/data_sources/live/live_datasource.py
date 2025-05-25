from abc import ABC, abstractmethod
from typing import Callable, Dict

class LiveDataSource(ABC):
    """Anything that can push you live ticks."""
    @abstractmethod
    def subscribe(self, callback: Callable[[Dict], None]) -> None:
        """
        Register `callback(tick_dict)` to be called on every new tick.
        tick_dict must contain at least:
            { 'time': int, 'price': float, 'volume': int, 'pair': str }
        """
        pass