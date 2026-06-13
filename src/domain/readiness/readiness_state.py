"""Canonical readiness states for the live trading system."""

from enum import Enum, auto


class ReadinessState(Enum):
    """
    Single source of truth for whether the system is ready to trade.

    States are ordered conceptually from least to most ready, but transitions
    are explicit and controlled by ReadinessStateMachine.
    """

    DISCONNECTED = auto()
    CONNECTED = auto()
    WAITING_FOR_HISTORY = auto()
    REFRESHING = auto()
    WARMING_UP = auto()
    READY = auto()
    LIVE = auto()
    DEGRADED = auto()
