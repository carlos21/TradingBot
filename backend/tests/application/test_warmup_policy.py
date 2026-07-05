"""Unit tests for MinimumBarsWarmupPolicy."""

from __future__ import annotations

from typing import Any

import pytest

from src.application.live_readiness.warmup_policy import MinimumBarsWarmupPolicy
from src.utils.app_logger import ILogger


class _FakeLogger(ILogger):
    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeStrategy:
    def __init__(self, timeframes: list[str], history: dict[str, list[dict[str, Any]]]):
        self.internal_timeframes = timeframes
        self._history = history
        self.logger = _FakeLogger()

    def get_history(self, tf: str, count: int) -> list[dict[str, Any]]:
        return self._history.get(tf, [])[-count:]


class TestMinimumBarsWarmupPolicy:
    def test_warm_when_all_timeframes_have_enough_bars(self) -> None:
        strategy = _FakeStrategy(
            timeframes=["1m", "5m"],
            history={
                "1m": [{"time": i} for i in range(30)],
                "5m": [{"time": i} for i in range(30)],
            },
        )
        policy = MinimumBarsWarmupPolicy(min_bars=30)
        assert policy.is_warm(strategy) is True

    def test_not_warm_when_one_timeframe_is_short(self) -> None:
        strategy = _FakeStrategy(
            timeframes=["1m", "5m"],
            history={
                "1m": [{"time": i} for i in range(30)],
                "5m": [{"time": i} for i in range(5)],
            },
        )
        policy = MinimumBarsWarmupPolicy(min_bars=30)
        assert policy.is_warm(strategy) is False

    def test_not_warm_when_timeframe_missing(self) -> None:
        strategy = _FakeStrategy(
            timeframes=["1m", "5m"],
            history={
                "1m": [{"time": i} for i in range(30)],
            },
        )
        policy = MinimumBarsWarmupPolicy(min_bars=30)
        assert policy.is_warm(strategy) is False

    def test_not_warm_with_empty_history(self) -> None:
        strategy = _FakeStrategy(timeframes=["1m"], history={})
        policy = MinimumBarsWarmupPolicy(min_bars=30)
        assert policy.is_warm(strategy) is False

    def test_min_bars_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="min_bars must be positive"):
            MinimumBarsWarmupPolicy(min_bars=0)

    def test_min_bars_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="min_bars must be positive"):
            MinimumBarsWarmupPolicy(min_bars=-1)

    def test_warm_with_more_than_min_bars(self) -> None:
        strategy = _FakeStrategy(
            timeframes=["1m"],
            history={"1m": [{"time": i} for i in range(100)]},
        )
        policy = MinimumBarsWarmupPolicy(min_bars=30)
        assert policy.is_warm(strategy) is True
