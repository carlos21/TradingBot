"""Replay historical bars through the strategy to warm up indicators."""

from __future__ import annotations

import threading
import time
import traceback
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
        self._stop_event = threading.Event()

    @property
    def strategy(self) -> LiquidityStrategyV2:
        return self._strategy

    def cancel(self) -> None:
        """Signal the running warmup to stop between bar replays."""
        self._stop_event.set()

    def reset_cancel(self) -> None:
        """Clear the cancel flag before starting a new warmup run."""
        self._stop_event.clear()

    def run(
        self,
        bars: list[dict[str, Any]],
        pair: str,
    ) -> bool:
        """
        Replay *bars* through the strategy.

        Callers must ensure the readiness state machine is already in
        WARMING_UP before calling this method, so the strategy sees
        is_warmup() == True and is_trading_enabled() == False.

        If ``cancel()`` is called while this method is running, the replay
        will stop at the next checkpoint and return early without calling
        the restore methods (the strategy will be reset by the caller anyway).

        Returns ``True`` if the replay completed successfully, ``False`` if it
        was cancelled or failed.
        """
        if not bars:
            if self._logger:
                self._logger.info("[Warmup] No historical bars to replay")
            return True

        if self._logger:
            self._logger.info(
                f"[Warmup] Replaying {len(bars)} historical bars through strategy..."
            )

        self._strategy.warmup_crossed_lines.clear()

        start_time = time.monotonic()
        progress_interval = max(1, len(bars) // 10)
        try:
            for i, bar in enumerate(bars):
                if self._stop_event.is_set():
                    if self._logger:
                        self._logger.info(
                            f"[Warmup] Cancelled after {i}/{len(bars)} bars — "
                            "skipping restore (strategy will be reset)"
                        )
                    return False
                self._strategy.on_raw_bar(bar)
                if self._logger and (i + 1) % progress_interval == 0:
                    self._logger.info(
                        f"[Warmup] Replay progress: {i + 1}/{len(bars)} bars"
                    )

            if self._logger:
                self._logger.info("[Warmup] Replay loop finished; restoring persisted state...")
            self._strategy.restore_trigger_states(pair)
            self._strategy.restore_open_trades()
            self._strategy.restore_reentry_opportunities(pair)

            if self._logger:
                self._logger.info("[Warmup] Removing stale lines touched during replay...")
            stale_count = self._remove_stale_lines()
            elapsed = time.monotonic() - start_time

            if self._logger:
                self._logger.info(
                    f"[Warmup] Complete in {elapsed:.1f}s — "
                    f"{len(bars)} bars replayed, {stale_count} stale line(s) removed"
                )
            return True
        except Exception as e:
            if self._logger:
                self._logger.error(f"[Warmup] Replay failed: {e}")
                self._logger.error(traceback.format_exc())
            return False

    def _remove_stale_lines(self) -> int:
        """Remove lines that were touched during warm-up."""
        strategy = self._strategy
        stale_count = 0
        for sid in list(strategy.warmup_crossed_lines):
            line = strategy.strategy_lines.get(sid)
            if line is None or line.get('interaction_ts') is None:
                # The line is already gone or was never really interacted with;
                # drop the tracking id so the set does not leak stale entries.
                strategy.warmup_crossed_lines.discard(sid)
                continue
            if strategy.options.line_removal_mode != LineRemovalMode.NEVER:
                strategy.remove_strategy_line(sid)
                stale_count += 1
            strategy.warmup_crossed_lines.discard(sid)
        return stale_count
