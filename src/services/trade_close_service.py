"""Single source of truth for ALL trade closing operations.

This module centralizes trade closing logic to eliminate duplication
across BaseLiquidityStrategy, TradeManager, and other components.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Dict, Literal, Optional, Protocol, Tuple

from src.financial_calc import FinancialCalc
from src.types import Direction
from src.repositories.trades_repository import TradeRepository


class CloseReason(Enum):
    """Enumeration of all possible trade close reasons."""
    STOP_LOSS_HIT = auto()
    TAKE_PROFIT_HIT = auto()
    SESSION_END = auto()
    MANUAL_CLOSE = auto()
    STREAM_END = auto()
    BROKER_FILL = auto()


@dataclass
class TradeCloseResult:
    """Result of closing a trade."""
    trade_id: str
    exit_price: float
    exit_time: datetime
    result_r: float
    result_type: Literal["BE", "SL", "TP", "SP"]
    fees: float
    pnl_usd: float
    close_reason: CloseReason


class TradeEventPublisher(Protocol):
    """Protocol for publishing trade events. Decouples from SocketIO."""
    
    def emit_trade_closed(self, trade_data: Dict) -> None:
        """Emit trade closed event to subscribers."""
        ...
    
    def emit_trade_updated(self, trade_id: str, updates: Dict) -> None:
        """Emit trade update event (e.g., SL moved to breakeven)."""
        ...


class NoOpTradeEventPublisher:
    """No-op implementation for testing/backtest without SocketIO."""
    
    def emit_trade_closed(self, trade_data: Dict) -> None:
        pass
    
    def emit_trade_updated(self, trade_id: str, updates: Dict) -> None:
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
        event_publisher: Optional[TradeEventPublisher] = None,
        point_value: float = 5.0,
        fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
    ):
        self.trade_repository = trade_repository
        self.event_publisher = event_publisher or NoOpTradeEventPublisher()
        self.point_value = point_value
        self.fee_per_rt = fee_per_rt
    
    def close_trade(
        self,
        trade: Dict,
        exit_price: float,
        exit_time: datetime,
        close_reason: CloseReason,
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
        
        # Use FinancialCalc as single source of truth
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
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
        
        # Special handling for session end
        if close_reason == CloseReason.SESSION_END:
            result_type = FinancialCalc.calculate_session_end_result_type(result_r)
        
        # Persist to database
        self.trade_repository.close_trade(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_time=exit_time,
            result=result_r,
            result_type=result_type,
            fees=fees,
            pnl_usd=pnl_usd,
        )
        
        # Build result
        result = TradeCloseResult(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_time=exit_time,
            result_r=result_r,
            result_type=result_type,
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
            'result_type': result_type,
            'fees': fees,
            'pnl_usd': pnl_usd,
            'close_reason': close_reason.name,
        })
        
        return result
    
    def check_sl_tp_hit(
        self,
        trade: Dict,
        bar: Dict,
        broker_mode: str = 'futures',
        broker_spread: float = 0.0,
    ) -> Optional[Tuple[float, CloseReason]]:
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
        trade: Dict,
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
        trade: Dict,
        exit_price: float,
        exit_time: datetime,
        result_type: Optional[str] = None,
    ) -> TradeCloseResult:
        """Close trade on broker fill (live mode).
        
        Args:
            trade: Trade dictionary
            exit_price: Fill price from broker
            exit_time: Fill timestamp
            result_type: Optional override (e.g., from broker signal)
        """
        result = self.close_trade(
            trade=trade,
            exit_price=exit_price,
            exit_time=exit_time,
            close_reason=CloseReason.BROKER_FILL,
        )
        
        # Override result_type if provided by broker
        if result_type:
            result.result_type = result_type  # type: ignore
        
        return result
