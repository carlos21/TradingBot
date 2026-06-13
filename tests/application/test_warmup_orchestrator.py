"""Unit tests for WarmupOrchestrator."""

from __future__ import annotations

from typing import Any

import pytest

from src.application.live_readiness.warmup_orchestrator import WarmupOrchestrator
from src.strategies.liquidity_v2.base_strategy import LineRemovalMode
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


class _FakeOptions:
    def __init__(self, line_removal_mode: LineRemovalMode = LineRemovalMode.NEVER):
        self.line_removal_mode = line_removal_mode


class _FakeStrategy:
    def __init__(self, line_removal_mode: LineRemovalMode = LineRemovalMode.NEVER) -> None:
        self.replayed_bars: list[dict[str, Any]] = []
        self.restore_calls: list[str] = []
        self.removed_lines: list[str] = []
        self.warmup_crossed_lines: set[str] = set()
        self.strategy_lines: dict[str, Any] = {}
        self.options = _FakeOptions(line_removal_mode)
        self.logger = _FakeLogger()

    def on_raw_bar(self, bar: dict[str, Any]) -> None:
        self.replayed_bars.append(bar)
        # Simulate crossing any configured strategy lines during warm-up replay.
        for sid, line in self.strategy_lines.items():
            if line.get("interaction_ts") is not None:
                self.warmup_crossed_lines.add(sid)

    def restore_trigger_states(self, pair: str) -> None:
        self.restore_calls.append(f"trigger_states:{pair}")

    def restore_open_trades(self) -> None:
        self.restore_calls.append("open_trades")

    def restore_reentry_opportunities(self, pair: str) -> None:
        self.restore_calls.append(f"reentry:{pair}")

    def remove_strategy_line(self, sid: str) -> None:
        self.removed_lines.append(sid)


def _make_bars(count: int) -> list[dict[str, Any]]:
    return [
        {
            "time": 1000 + i * 60,
            "open": 1.0,
            "high": 2.0,
            "low": 0.0,
            "close": 1.0,
            "volume": 1,
            "pair": "MNQ",
        }
        for i in range(count)
    ]


class TestWarmupOrchestrator:
    def test_replays_bars_through_strategy(self) -> None:
        strategy = _FakeStrategy()
        orchestrator = WarmupOrchestrator(strategy, logger=_FakeLogger())
        bars = _make_bars(5)
        orchestrator.run(bars, "MNQ")
        assert strategy.replayed_bars == bars

    def test_calls_restore_methods(self) -> None:
        strategy = _FakeStrategy()
        orchestrator = WarmupOrchestrator(strategy, logger=_FakeLogger())
        orchestrator.run(_make_bars(3), "MNQ")
        assert strategy.restore_calls == [
            "trigger_states:MNQ",
            "open_trades",
            "reentry:MNQ",
        ]

    def test_handles_empty_bar_list(self) -> None:
        strategy = _FakeStrategy()
        orchestrator = WarmupOrchestrator(strategy, logger=_FakeLogger())
        orchestrator.run([], "MNQ")
        assert strategy.replayed_bars == []
        assert strategy.restore_calls == []

    def test_removes_stale_lines_when_not_never(self) -> None:
        strategy = _FakeStrategy(line_removal_mode=LineRemovalMode.ON_EVALUATE)
        strategy.warmup_crossed_lines = {"line1", "line2"}
        strategy.strategy_lines = {
            "line1": {"interaction_ts": 1},
            "line2": {"interaction_ts": 2},
        }
        orchestrator = WarmupOrchestrator(strategy, logger=_FakeLogger())
        orchestrator.run(_make_bars(3), "MNQ")
        assert set(strategy.removed_lines) == {"line1", "line2"}
        assert strategy.warmup_crossed_lines == set()

    def test_does_not_remove_lines_when_never(self) -> None:
        strategy = _FakeStrategy(line_removal_mode=LineRemovalMode.NEVER)
        strategy.warmup_crossed_lines = {"line1"}
        strategy.strategy_lines = {"line1": {"interaction_ts": 1}}
        orchestrator = WarmupOrchestrator(strategy, logger=_FakeLogger())
        orchestrator.run(_make_bars(3), "MNQ")
        assert strategy.removed_lines == []
        assert strategy.warmup_crossed_lines == set()

    def test_skips_missing_lines(self) -> None:
        strategy = _FakeStrategy(line_removal_mode=LineRemovalMode.ON_EVALUATE)
        strategy.warmup_crossed_lines = {"missing", "present"}
        strategy.strategy_lines = {"present": {"interaction_ts": 1}}
        orchestrator = WarmupOrchestrator(strategy, logger=_FakeLogger())
        orchestrator.run(_make_bars(3), "MNQ")
        assert strategy.removed_lines == ["present"]

    def test_skips_lines_without_interaction(self) -> None:
        strategy = _FakeStrategy(line_removal_mode=LineRemovalMode.ON_EVALUATE)
        strategy.warmup_crossed_lines = {"no_interaction"}
        strategy.strategy_lines = {"no_interaction": {}}
        orchestrator = WarmupOrchestrator(strategy, logger=_FakeLogger())
        orchestrator.run(_make_bars(3), "MNQ")
        assert strategy.removed_lines == []
