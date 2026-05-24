"""TsiCrossStrategy — line-less strategy based on 5m TSI crosses.

Bullish TSI cross → long, SL at previous low bounce.
Bearish TSI cross → short, SL at previous high bounce.

Composed of small, focused, independently tweakable components:
  - TsiAnalyzer        : pure TSI math
  - BounceDetector     : finds swing lows/highs for SL placement
  - Entry filters      : reusable filter pipeline
"""

from __future__ import annotations

import dataclasses
from collections import deque
from typing import Any

from src.application.ports import EventPublisher
from src.domain.types import Direction
from src.financial_calc import FinancialCalc
from src.services.trade_manager import TradeManager
from src.strategies.base_strategy import BaseStrategy, BreakevenConfig
from src.strategies.entry_context import EntryContext
from src.strategies.tsi_cross.bounce_detector import BounceDetector, SwingBounceDetector
from src.strategies.tsi_cross.config import TsiCrossConfig, TsiCrossNumbers
from src.strategies.tsi_cross.tsi_analyzer import TsiAnalyzer
from src.utils.app_logger import ILogger


class TsiCrossStrategy(BaseStrategy):
    """Line-less strategy that enters on 5m TSI crosses.

    Configuration is split into:
      - ``numbers`` : SL tiers, risk, position sizing
      - ``config``  : TSI lengths, bounce detection, filters
    """

    def __init__(
        self,
        numbers: TsiCrossNumbers,
        config: TsiCrossConfig | None = None,
        bounce_detector: BounceDetector | None = None,
        socketio: EventPublisher | None = None,
        trade_repository=None,
        trade_manager: TradeManager | None = None,
        trade_logger=None,
        analytics=None,
        logger: ILogger | None = None,
        account_configs=None,
        accounts_repo=None,
    ):
        self._tsi_config = config or TsiCrossConfig()
        self._tsi_numbers = numbers

        super().__init__(
            min_stop_loss=numbers.min_stop_loss,
            socketio=socketio,
            trade_repository=trade_repository,
            trade_manager=trade_manager,
            extra_sl_space=numbers.extra_sl_space,
            point_value=numbers.point_value,
            account_balance=numbers.account_balance,
            risk_per_trade=numbers.risk_per_trade,
            risk_pct_per_trade=numbers.risk_pct_per_trade,
            fixed_stop_loss=numbers.fixed_stop_loss,
            max_stop_loss=numbers.max_stop_loss,
            strategy_tf=self._tsi_config.timeframe,
            sl_levels=numbers.sl_levels,
            rr_ratio=numbers.rr_ratio,
            use_fractional_lots=numbers.use_fractional_lots,
            fee_per_rt=numbers.fee_per_rt,
            broker_spread=numbers.broker_spread,
            trade_logger=trade_logger,
            analytics=analytics,
            logger=logger,
            account_configs=account_configs,
            accounts_repo=accounts_repo,
        )

        self._analyzer = TsiAnalyzer(
            long_len=self._tsi_config.tsi_long_len,
            short_len=self._tsi_config.tsi_short_len,
            signal_len=self._tsi_config.tsi_signal_len,
            confirmation_bars=self._tsi_config.cross_confirmation_bars,
        )

        self._bounce_detector = bounce_detector or SwingBounceDetector(
            lookback=self._tsi_config.bounce_lookback,
            neighbor_bars=self._tsi_config.bounce_neighbor_bars,
        )

        self.entry_filters = list(self._tsi_config.entry_filters or [])

        # Rolling history of completed strategy-TF bars
        self._history: deque[dict[str, Any]] = deque(maxlen=200)

    def reset(self, preserve_trigger_state: bool = False):
        super().reset(preserve_trigger_state=preserve_trigger_state)
        self._history.clear()

    # ------------------------------------------------------------------
    # Trade creation override — opposite-cross exit mode
    # ------------------------------------------------------------------

    def _build_trade_from_context(self, ctx: EntryContext) -> dict[str, Any]:
        """Build trade dict; when ``close_on_opposite_cross`` is set,
        take-profit is None (exit is driven by the next opposite cross)."""
        trade = super()._build_trade_from_context(ctx)
        if self._tsi_numbers.close_on_opposite_cross:
            trade['take_profit'] = None
            trade['close_on_opposite_cross'] = True
        return trade

    def _close_on_opposite_cross(self, bar: dict[str, Any], cross_direction: Direction):
        """Close any open trade that is opposite to the detected cross direction."""
        for trade in list(self.open_trades):
            if trade.get('status') != 'open':
                continue
            trade_type = trade['type']
            is_opposite = (
                (trade_type == 'long' and cross_direction == Direction.SHORT)
                or (trade_type == 'short' and cross_direction == Direction.LONG)
            )
            if not is_opposite:
                continue

            exit_price = bar['close']
            trade_id = trade.get('trade_id')

            if self.trade_manager and trade_id:
                self.trade_manager.close_trade(trade_id, exit_price, bar['time'])

            # Always update strategy's own list (trade_manager may not touch it)
            trade['status'] = 'closed'
            trade['exit_price'] = exit_price
            trade['exit_time'] = bar['time']
            self.open_trades = [
                t for t in self.open_trades
                if t.get('trade_id') != trade_id
            ]

    # ------------------------------------------------------------------
    # Core bar processing
    # ------------------------------------------------------------------

    def _on_strategy_bar(self, bar: dict[str, Any]):
        """Process each completed strategy-TF bar.

        1. Append to history
        2. Calculate TSI & detect cross
        3. If opposite cross → close existing trade (when enabled)
        4. If cross → find bounce → build context → run filters → open trade
        5. Check breakeven & phantom exits on existing trades
        """
        self._history.append(bar)

        # Need enough bars for TSI calculation
        closes = [b["close"] for b in self._history]
        result = self._analyzer.analyze(closes)

        if result.direction is not None:
            if self._tsi_numbers.close_on_opposite_cross:
                self._close_on_opposite_cross(bar, result.direction)
            self._evaluate_cross(bar, result.direction)

        # Post-trade maintenance
        self._check_breakeven(bar)
        self._check_phantom_exits(bar)

    def _evaluate_cross(self, bar: dict[str, Any], direction: Direction):
        """Given a TSI cross direction, try to open a trade."""
        if direction == Direction.LONG:
            bounce_price = self._bounce_detector.find_previous_low_bounce(list(self._history))
        else:
            bounce_price = self._bounce_detector.find_previous_high_bounce(list(self._history))

        entry_price = bar["close"]

        # cross_depth = distance from entry to SL reference (used by filters if needed)
        if direction == Direction.LONG:
            cross_depth = max(0.0, entry_price - bounce_price)
        else:
            cross_depth = max(0.0, bounce_price - entry_price)

        ctx = EntryContext(
            strategy=self,
            line_id=None,
            direction=direction,
            level=entry_price,
            bar=bar,
            close=entry_price,
            low=bar["low"],
            high=bar["high"],
            extreme=bounce_price,
            cross_depth=cross_depth,
        )

        allow, reason, _ = self._filters_allow_entry(ctx)
        if not allow:
            self.log_decision(
                bar["time"], bar.get("tf"), None, "FILTER_BLOCK",
                f"{reason} | bounce={bounce_price:.2f}",
                direction=str(direction),
            )
            return

        trade = self._build_trade_from_context(ctx)
        if self._tsi_config.breakeven:
            trade["breakeven_config"] = dataclasses.asdict(self._tsi_config.breakeven)

        sl_str = f"{trade['stop_loss']:.2f}"
        tp_str = f"{trade['take_profit']:.2f}" if trade.get('take_profit') is not None else "OPPOSITE_CROSS"
        self.log_decision(
            bar["time"], bar.get("tf"), None, "ENTRY",
            f"{direction} @ {entry_price:.2f} | SL={sl_str} | TP={tp_str}",
            direction=str(direction),
        )
        self._store_and_emit_open(trade)
