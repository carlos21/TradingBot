"""Single source of truth for ALL trade closing operations.

This module centralizes trade closing logic to eliminate duplication
across BaseLiquidityStrategy, TradeManager, and other components.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from src.domain.repositories import TradeRepository
from src.domain.result_type_classifier import ClassificationContext, DefaultResultTypeClassifier, ResultTypeClassifier
from src.domain.types import CloseReason, Direction, ResultType
from src.financial_calc import FinancialCalc

# Re-export for backward compatibility with existing callers/tests.
__all__ = ["CloseReason", "TradeCloseService", "TradeCloseResult", "TradeEventPublisher", "NoOpTradeEventPublisher"]


@dataclass
class TradeCloseResult:
    """Result of closing a trade."""
    trade_id: str
    exit_price: float
    exit_time: datetime
    result_r: float
    result_type: Literal["BE", "SL", "TP", "SP", "CLOSE"]
    fees: float
    pnl_usd: float
    close_reason: CloseReason


class TradeEventPublisher(Protocol):
    """Protocol for publishing trade events. Decouples from SocketIO."""

    def emit_trade_closed(self, trade_data: dict) -> None:
        """Emit trade closed event to subscribers."""
        ...

    def emit_trade_updated(self, trade_id: str, updates: dict) -> None:
        """Emit trade update event (e.g., SL moved to breakeven)."""
        ...


class NoOpTradeEventPublisher:
    """No-op implementation for testing/backtest without SocketIO."""

    def emit_trade_closed(self, trade_data: dict) -> None:
        pass

    def emit_trade_updated(self, trade_id: str, updates: dict) -> None:
        pass


class TradeCloseService:
    """Centralized service for closing trades.

    This is the SINGLE SOURCE OF TRUTH for:
    - Financial calculations (R, fees, PnL)
    - Result type determination (SL/TP/BE/SP)
    - Database persistence
    - Event emission

    All trade closing operations should go through this service.
    """

    def __init__(
        self,
        trade_repository: TradeRepository,
        event_publisher: TradeEventPublisher | None = None,
        point_value: float = 5.0,
        fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
        result_type_classifier: ResultTypeClassifier | None = None,
    ):
        self.trade_repository = trade_repository
        self.event_publisher = event_publisher or NoOpTradeEventPublisher()
        self.point_value = point_value
        self.fee_per_rt = fee_per_rt
        self._classifier = result_type_classifier or DefaultResultTypeClassifier()

    def close_trade(
        self,
        trade: dict,
        exit_price: float,
        exit_time: datetime,
        close_reason: CloseReason,
        broker_result_type: str | None = None,
    ) -> TradeCloseResult:
        """Close a trade with centralized logic.

        Args:
            trade: Trade dictionary with all required fields
            exit_price: Price at which trade is closing
            exit_time: Timestamp of close
            close_reason: Why the trade is closing

        Returns:
            TradeCloseResult with all calculated fields
        """
        # Extract trade data
        trade_id = trade['trade_id']
        trade_type = trade['type']
        entry_price = trade['entry']
        stop_loss = trade['stop_loss']
        take_profit = trade['take_profit']
        risk_points = trade.get('risk', 0) or 1.0
        contracts = trade.get('contracts', 1) or 1

        # Use FinancialCalc for PnL / R metrics; classifier for result type.
        result_r, fees, pnl_usd, _ = FinancialCalc.calculate_close_metrics(
            direction=Direction.from_string(trade_type),
            entry_price=entry_price,
            exit_price=exit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_points=risk_points,
            contracts=contracts,
            point_value=self.point_value,
            fee_per_rt=self.fee_per_rt,
        )

        result_type = self._classifier.classify(
            ClassificationContext(
                direction=Direction.from_string(trade_type),
                entry_price=entry_price,
                exit_price=exit_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                close_reason=close_reason,
                broker_result_type=broker_result_type,
            )
        )
        result_type_str = result_type.value

        # Persist to database
        self.trade_repository.close_trade(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_time=exit_time,
            result=result_r,
            result_type=result_type_str,
            fees=fees,
            pnl_usd=pnl_usd,
        )

        # Build result
        result = TradeCloseResult(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_time=exit_time,
            result_r=result_r,
            result_type=result_type_str,
            fees=fees,
            pnl_usd=pnl_usd,
            close_reason=close_reason,
        )

        # Emit event
        self.event_publisher.emit_trade_closed({
            'trade_id': trade_id,
            'pair': trade.get('pair', ''),
            'type': trade_type,
            'exit_price': exit_price,
            'exit_time': exit_time.timestamp() if isinstance(exit_time, datetime) else exit_time,
            'result': result_r,
            'result_type': result_type_str,
            'fees': fees,
            'pnl_usd': pnl_usd,
            'close_reason': close_reason.name,
        })

        return result

    def check_sl_tp_hit(
        self,
        trade: dict,
        bar: dict,
        _broker_mode: str = 'futures',
        _broker_spread: float = 0.0,
    ) -> tuple[float, CloseReason] | None:
        """Check if SL or TP was hit by a bar.

        Args:
            trade: Trade dictionary
            bar: Bar dictionary with 'high', 'low'
            broker_mode: 'futures' or 'cfd'
            broker_spread: Spread for CFD mode

        Returns:
            Tuple of (exit_price, close_reason) if hit, None otherwise
        """
        trade_type = trade['type']
        stop_loss = trade['stop_loss']
        take_profit = trade['take_profit']

        is_long = trade_type == 'long'
        is_short = trade_type == 'short'

        bar_low = bar['low']
        bar_high = bar['high']

        if is_long:
            if bar_low <= stop_loss:
                return stop_loss, CloseReason.STOP_LOSS_HIT
            elif bar_high >= take_profit:
                return take_profit, CloseReason.TAKE_PROFIT_HIT

        elif is_short:
            if bar_high >= stop_loss:
                return stop_loss, CloseReason.STOP_LOSS_HIT
            elif bar_low <= take_profit:
                return take_profit, CloseReason.TAKE_PROFIT_HIT

        return None

    def close_at_session_end(
        self,
        trade: dict,
        exit_price: float,
        exit_time: datetime,
    ) -> TradeCloseResult:
        """Convenience method for session end closes."""
        return self.close_trade(
            trade=trade,
            exit_price=exit_price,
            exit_time=exit_time,
            close_reason=CloseReason.SESSION_END,
        )

    def close_on_broker_fill(
        self,
        trade: dict,
        exit_price: float,
        exit_time: datetime,
        result_type: str | None = None,
    ) -> TradeCloseResult:
        """Close trade on broker fill (live mode).

        Args:
            trade: Trade dictionary
            exit_price: Fill price from broker
            exit_time: Fill timestamp
            result_type: Optional broker-provided result type (e.g. 'SL', 'TP', 'CLOSE')
        """
        return self.close_trade(
            trade=trade,
            exit_price=exit_price,
            exit_time=exit_time,
            close_reason=CloseReason.BROKER_FILL,
            broker_result_type=result_type,
        )
