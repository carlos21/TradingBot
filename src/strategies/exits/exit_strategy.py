"""Exit strategy pattern for trade exit logic.

This module provides the Strategy pattern for different exit behaviors:
- SL/TP checking (backtest mode)
- Session end close
- No exit checking (live mode - broker handles it)

This eliminates the LSP violation where LiveLiquidityStrategyV2
overrides methods to do nothing.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Dict, List, Optional, Protocol, Tuple

from zoneinfo import ZoneInfo

from src.types import Direction


class ExitType(Enum):
    """Type of exit that occurred."""
    STOP_LOSS = auto()
    TAKE_PROFIT = auto()
    SESSION_END = auto()
    TRAILING_STOP = auto()
    TIME_BASED = auto()


@dataclass
class ExitSignal:
    """Signal that a trade should be exited."""
    exit_type: ExitType
    exit_price: float
    reason: str


class ExitStrategy(ABC):
    """Abstract base class for exit strategies.
    
    Implementations determine when and how trades should be exited.
    This allows different behaviors for backtest vs live mode.
    """
    
    @abstractmethod
    def check_exit(
        self,
        trade: Dict,
        bar: Dict,
    ) -> Optional[ExitSignal]:
        """Check if trade should be exited based on current bar.
        
        Args:
            trade: Trade dictionary with entry, SL, TP, etc.
            bar: Current bar dictionary with high, low, close
            
        Returns:
            ExitSignal if exit should occur, None otherwise
        """
        pass
    
    def on_bar(self, bar: Dict, open_trades: List[Dict]) -> List[Tuple[Dict, ExitSignal]]:
        """Check all open trades for exit signals.
        
        Args:
            bar: Current bar dictionary
            open_trades: List of open trade dictionaries
            
        Returns:
            List of (trade, exit_signal) tuples for trades that should exit
        """
        exits = []
        for trade in open_trades:
            signal = self.check_exit(trade, bar)
            if signal:
                exits.append((trade, signal))
        return exits


class SLTPExitStrategy(ExitStrategy):
    """Standard stop loss / take profit exit strategy.
    
    Used in backtest mode to check if price hits SL or TP levels.
    """
    
    def __init__(
        self,
        broker_mode: str = 'futures',
        broker_spread: float = 0.0,
    ):
        self.broker_mode = broker_mode
        self.broker_spread = broker_spread
    
    def check_exit(
        self,
        trade: Dict,
        bar: Dict,
    ) -> Optional[ExitSignal]:
        """Check if SL or TP was hit."""
        direction = trade['direction']  # Direction enum
        stop_loss = trade['stop_loss']
        take_profit = trade['take_profit']
        
        # Adjust for spread in CFD mode
        spread_adj = self.broker_spread / 2.0 if self.broker_mode == 'cfd' else 0.0
        adjusted_sl = stop_loss
        adjusted_tp = take_profit
        
        if self.broker_mode == 'cfd':
            if direction.is_long:
                adjusted_sl = stop_loss - spread_adj
                adjusted_tp = take_profit - spread_adj
            else:  # SHORT
                adjusted_sl = stop_loss + spread_adj
                adjusted_tp = take_profit + spread_adj
        
        bar_low = bar['low']
        bar_high = bar['high']
        
        if direction.is_long:
            if bar_low <= adjusted_sl:
                return ExitSignal(
                    exit_type=ExitType.STOP_LOSS,
                    exit_price=stop_loss,
                    reason=f"SL hit: low {bar_low} <= adjusted SL {adjusted_sl:.2f}",
                )
            elif bar_high >= adjusted_tp:
                return ExitSignal(
                    exit_type=ExitType.TAKE_PROFIT,
                    exit_price=take_profit,
                    reason=f"TP hit: high {bar_high} >= adjusted TP {adjusted_tp:.2f}",
                )
                
        else:  # SHORT
            if bar_high >= adjusted_sl:
                return ExitSignal(
                    exit_type=ExitType.STOP_LOSS,
                    exit_price=stop_loss,
                    reason=f"SL hit: high {bar_high} >= adjusted SL {adjusted_sl:.2f}",
                )
            elif bar_low <= adjusted_tp:
                return ExitSignal(
                    exit_type=ExitType.TAKE_PROFIT,
                    exit_price=take_profit,
                    reason=f"TP hit: low {bar_low} <= adjusted TP {adjusted_tp:.2f}",
                )
        
        return None


class SessionEndExitStrategy(ExitStrategy):
    """Exit strategy that closes trades at session end time.
    
    Used to close all open trades at a specific time (e.g., end of trading day).
    """
    
    def __init__(
        self,
        session_end_time: str,  # "HH:MM" format
        session_tz: str,  # e.g., "America/New_York"
    ):
        self.session_end_time = datetime.strptime(session_end_time, "%H:%M").time()
        self.session_tz = ZoneInfo(session_tz)
    
    def check_exit(
        self,
        trade: Dict,
        bar: Dict,
    ) -> Optional[ExitSignal]:
        """Check if session end time has been reached."""
        bar_dt = datetime.fromtimestamp(bar["time"], tz=self.session_tz)
        
        if bar_dt.time() < self.session_end_time:
            return None
        
        # Check if trade entry is before this bar (don't close trades entered after session end)
        if trade.get('entry_time', 0) > bar['time']:
            return None
        
        return ExitSignal(
            exit_type=ExitType.SESSION_END,
            exit_price=bar['close'],
            reason=f"Session end at {bar_dt.time()} {self.session_tz}",
        )


class NoExitStrategy(ExitStrategy):
    """No-op exit strategy for live mode.
    
    In live mode, the broker (NinjaTrader) handles SL/TP/Session end.
    This strategy never signals an exit.
    """
    
    def check_exit(
        self,
        trade: Dict,
        bar: Dict,
    ) -> Optional[ExitSignal]:
        """Never signals an exit - broker handles it."""
        return None


class CompositeExitStrategy(ExitStrategy):
    """Combines multiple exit strategies.
    
    Checks strategies in order and returns the first exit signal found.
    Useful for combining SLTP + SessionEnd checking.
    """
    
    def __init__(self, strategies: List[ExitStrategy]):
        self.strategies = strategies
    
    def check_exit(
        self,
        trade: Dict,
        bar: Dict,
    ) -> Optional[ExitSignal]:
        """Check all strategies in order."""
        for strategy in self.strategies:
            signal = strategy.check_exit(trade, bar)
            if signal:
                return signal
        return None
