from abc import ABC, abstractmethod
from typing import List, Dict

class BarsDataSource(ABC):
    """Generic interface for anything that can produce 1-minute bars."""
    @abstractmethod
    def load_1m_bars(self) -> List[Dict]:
        """
        Return a list of bars in the form:
        { 'time': int, 'open': float, 'high': float, 'low': float,
          'close': float, 'volume': int, 'pair': str }
        """
        pass