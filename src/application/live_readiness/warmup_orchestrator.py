"""Replay historical bars through the strategy to warm up indicators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.strategies.liquidity_v2.base_strategy import LineRemovalMode

if TYPE_CHECKING:
    from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
    from src.utils.app_logger import ILogger


class WarmupOrchestrator:
    """
    Runs the strategy in warm-up mode over a batch of historical bars.

    The orchestrator does not make readiness decisions itself; it only
    reports back to the ReadinessMonitor when the replay is finished.
    """

    def __init__(
        self,
        strategy: LiquidityStrategyV2,
        logger: ILogger | None = None,
    ) -> None:
        self._strategy = strategy
        self._logger = logger

    @property
    def strategy(self) -> LiquidityStrategyV2:
        return self._strategy

    def run(
        self,
        bars: list[dict[str, Any]],
        pair: str,
    ) -> None:
        """
        Replay *bars* through the strategy.

        Callers must ensure the readiness state machine is already in
        WARMING_UP before calling this method, so the strategy sees
        is_warmup() == True and is_trading_enabled() == False.
        """
        if not bars:
            if self._logger:
                self._logger.info("[Warmup] No historical bars to replay")
            return

        if self._logger:
            self._logger.info(
                f"[Warmup] Replaying {len(bars)} historical bars through strategy..."
            )

        self._strategy.warmup_crossed_lines.clear()

        start_time = __import__('time').monotonic()
        for bar in bars:
            self._strategy.on_raw_bar(bar)

        self._strategy.restore_trigger_states(pair)
        self._strategy.restore_open_trades()
        self._strategy.restore_reentry_opportunities(pair)

        stale_count = self._remove_stale_lines()
        elapsed = __import__('time').monotonic() - start_time

        if self._logger:
            self._logger.info(
                f"[Warmup] Complete in {elapsed:.1f}s — "
                f"{len(bars)} bars replayed, {stale_count} stale line(s) removed"
            )

    def _remove_stale_lines(self) -> int:
        """Remove lines that were touched during warm-up."""
        strategy = self._strategy
        stale_count = 0
        for sid in list(strategy.warmup_crossed_lines):
            line = strategy.strategy_lines.get(sid)
            if line is None:
                continue
            if line.get('interaction_ts') is None:
                continue
            if strategy.options.line_removal_mode != LineRemovalMode.NEVER:
                strategy.remove_strategy_line(sid)
                stale_count += 1
            strategy.warmup_crossed_lines.discard(sid)
        return stale_count
