"""Shared type definitions for the fetcher package."""

from typing import TypedDict


class Bar(TypedDict):
    """A single 1-minute OHLCV bar."""
    time:   int    # UTC epoch seconds
    open:   float
    high:   float
    low:    float
    close:  float
    volume: int
    pair:   str
