from abc import ABC, abstractmethod
from collections.abc import Callable


class CombinedDataSource(ABC):
    pair: str

    @abstractmethod
    def load_historical_bars(
        self,
        timeframe: str = '1m',
        start_time: int | None = None,
        end_time: int | None = None,
        pair: str | None = None,
    ) -> list[dict]:
        """
        Return a list of bar‐dicts:
          { time, open, high, low, close, volume, pair }

        Optional ``start_time`` and ``end_time`` filter the returned bars by epoch.
        Optional ``pair`` selects the instrument cache for multi-instrument sources.
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

    def request_refresh(self, days: int | None = None) -> None:
        """Request a fresh batch of historical bars from the platform.

        No-op by default; only live platform sources (e.g. ZMQDataSource)
        implement a real refresh request.
        """
        ...
