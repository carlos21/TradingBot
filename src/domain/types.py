"""Core type definitions for the trading bot.

This module provides enums to replace string-based type checking.
"""

from enum import Enum


class Direction(Enum):
    """Trade direction enumeration.

    Replaces string-based direction ('long', 'short') with type-safe enum.

    Usage:
        direction = Direction.LONG

        # Pattern matching (Python 3.10+)
        match direction:
            case Direction.LONG:
                ...
            case Direction.SHORT:
                ...

        # Boolean checks
        if direction.is_long:
            # Handle long direction
    """
    LONG = "long"
    SHORT = "short"

    @classmethod
    def from_string(cls, value: str) -> "Direction":
        """Create Direction from string.

        Args:
            value: String direction value ('long' or 'short')

        Returns:
            Direction enum value

        Raises:
            ValueError: If string doesn't match any direction
        """
        normalized = value.lower().strip()

        if normalized == "long":
            return cls.LONG
        elif normalized == "short":
            return cls.SHORT
        else:
            raise ValueError(f"Invalid direction: {value!r}. Use 'long' or 'short'")

    @property
    def is_long(self) -> bool:
        """Check if direction is LONG."""
        return self == Direction.LONG

    @property
    def is_short(self) -> bool:
        """Check if direction is SHORT."""
        return self == Direction.SHORT

    def opposite(self) -> "Direction":
        """Get the opposite direction.

        Returns:
            Direction.LONG if SHORT, Direction.SHORT if LONG
        """
        return Direction.SHORT if self == Direction.LONG else Direction.LONG

    def sign(self) -> int:
        """Get mathematical sign for direction.

        Returns:
            1 for LONG, -1 for SHORT
        """
        return 1 if self == Direction.LONG else -1

    def __str__(self) -> str:
        """Return string representation."""
        return self.value


class ResultType(Enum):
    """Trade result type enumeration.

    Replaces string-based result types ('SL', 'TP', 'BE', 'SP').
    """
    STOP_LOSS = "SL"
    TAKE_PROFIT = "TP"
    BREAKEVEN = "BE"
    MANUAL = "SP"  # Session end, manual close

    @classmethod
    def from_string(cls, value: str) -> "ResultType":
        """Create ResultType from string."""
        normalized = value.upper().strip()

        mapping = {
            'SL': cls.STOP_LOSS,
            'TP': cls.TAKE_PROFIT,
            'BE': cls.BREAKEVEN,
            'SP': cls.MANUAL,
            'STOP_LOSS': cls.STOP_LOSS,
            'TAKE_PROFIT': cls.TAKE_PROFIT,
            'BREAKEVEN': cls.BREAKEVEN,
            'MANUAL': cls.MANUAL,
        }

        if normalized in mapping:
            return mapping[normalized]
        raise ValueError(f"Invalid result type: {value!r}")


class TradeSource(Enum):
    """Trade source enumeration.

    Distinguishes how a trade was originated so filters can treat
    manual/test trades differently from strategy-generated trades.
    """
    STRATEGY = "strategy"
    MANUAL = "manual"
    TEST = "test"
    BROKER_SYNC = "broker_sync"

    @classmethod
    def from_string(cls, value: str) -> "TradeSource":
        """Create TradeSource from string."""
        normalized = value.lower().strip()
        for src in cls:
            if src.value == normalized:
                return src
        raise ValueError(f"Invalid trade source: {value!r}")

    def __str__(self) -> str:
        """Return string representation."""
        return self.value


class TimeFrame(Enum):
    """Standard timeframe enumeration.

    Provides type-safe timeframes with second conversions.
    """
    M1 = ("1m", 60)
    M3 = ("3m", 180)
    M5 = ("5m", 300)
    M15 = ("15m", 900)
    M30 = ("30m", 1800)
    H1 = ("1h", 3600)
    H4 = ("4h", 14400)
    D1 = ("1d", 86400)

    def __init__(self, label: str, seconds: int):
        self.label = label
        self.seconds = seconds

    @classmethod
    def from_string(cls, value: str) -> "TimeFrame":
        """Create TimeFrame from string like '5m' or '1h'."""
        normalized = value.lower().strip()
        for tf in cls:
            if tf.label == normalized:
                return tf
        raise ValueError(f"Invalid timeframe: {value!r}")

    def to_seconds(self) -> int:
        """Get timeframe in seconds."""
        return self.seconds
