"""Abstract base class for all market data providers."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List

from .types import Bar


class FetchProvider(ABC):
    """
    Interface that every data provider must implement.

    A provider is responsible for fetching 1-minute OHLCV bars from a
    specific data source (Yahoo Finance, Polygon.io, Alpaca, IBKR, etc.)
    and returning them as a list of Bar dicts with UTC epoch timestamps.

    To add a new provider:
      1. Subclass FetchProvider
      2. Implement fetch_bars() and the name property
      3. Optionally override max_history_days if the provider has a limit
      4. Register it in fetcher/run.py
    """

    @abstractmethod
    def fetch_bars(self, symbol: str, start: datetime, end: datetime) -> List[Bar]:
        """
        Fetch 1-minute OHLCV bars for the given symbol and time range.

        Args:
            symbol: Provider-specific symbol identifier.
                    Examples: "NQ=F" (yfinance), "NQ:XCME" (Polygon)
            start:  Timezone-aware UTC datetime (inclusive).
            end:    Timezone-aware UTC datetime (exclusive).

        Returns:
            List of Bar dicts sorted ascending by time (UTC epoch int).
            Returns an empty list if no data is available for the range.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (used in logs and CLI output)."""
        ...

    @property
    def max_history_days(self) -> int:
        """
        Maximum number of calendar days of 1m history this provider can fetch.
        -1 means unlimited (provider supports full history).
        """
        return -1
