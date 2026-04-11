"""
ZeroMQ Trade Executor - Integrates with TradeManager.

This module provides a TradeExecutor implementation that uses the
ZeroMQ gateway to send trade commands to the platform.
"""

from typing import Optional

from src.services.trade_executor import TradeExecutor
from src.utils.app_logger import ILogger, ConsoleLogger
from .gateway import TradingGateway


class ZMQTradeExecutor(TradeExecutor):
    """
    TradeExecutor that sends commands via ZeroMQ.
    
    This integrates with the existing TradeManager and sends
    trade commands to NinjaTrader, MetaTrader, or other platforms
    through the ZeroMQ gateway.
    
    Usage:
        gateway = TradingGateway()
        gateway.start()
        
        executor = ZMQTradeExecutor(gateway, risk_usd=500)
        trade_manager = TradeManager(..., trade_executor=executor)
    """
    
    def __init__(
        self,
        gateway: TradingGateway,
        *,
        risk_usd: Optional[float] = None,
        risk_pct: Optional[float] = None,
        logger: Optional[ILogger] = None,
    ):
        """
        Initialize the ZMQ trade executor.
        
        Args:
            gateway: The TradingGateway instance
            risk_usd: Fixed dollar risk per trade (optional)
            risk_pct: Percentage of account risk per trade (optional)
        """
        self._gateway = gateway
        self._risk_usd = risk_usd
        self._risk_pct = risk_pct
        self.logger = logger or ConsoleLogger()
    
    def on_trade_open(self, trade: dict) -> None:
        """
        Called when a new trade is opened.
        Sends open order command to the platform.
        
        Args:
            trade: Trade dict with keys:
                - trade_id: str
                - pair: str
                - type: "long" or "short"
                - entry: float
                - stop_loss: float
                - take_profit: float
                - risk: float (distance in points)
                - rr_ratio: float (optional)
        """
        try:
            # Calculate risk points
            entry = trade.get("entry", trade.get("entry_price", 0))
            sl = trade.get("stop_loss", trade.get("sl", 0))
            tp = trade.get("take_profit", trade.get("tp", 0))
            risk_points = trade.get("risk", abs(entry - sl))
            
            # Get RR ratio (default 5.0 for your system)
            rr_ratio = trade.get("rr_ratio", 5.0)
            
            self._gateway.send_open_order(
                trade_id=trade["trade_id"],
                direction=trade["type"],
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                risk_points=risk_points,
                rr_ratio=rr_ratio,
                pair=trade.get("pair"),
                risk_usd=self._risk_usd,
                risk_pct=self._risk_pct,
            )
            self.logger.info(f"Sent open order for trade {trade['trade_id']}")
            
        except Exception as e:
            self.logger.error(f"Error sending open order: {e}")
            raise
    
    def on_trade_close(self, trade_id: str, exit_price: float) -> None:
        """
        Called when a trade should be closed.
        Sends close order command to the platform.
        
        Args:
            trade_id: The trade ID to close
            exit_price: The exit price (for logging, platform determines actual fill)
        """
        try:
            self._gateway.send_close_order(trade_id=trade_id, reason="strategy")
            self.logger.info(f"Sent close order for trade {trade_id}")
            
        except Exception as e:
            self.logger.error(f"Error sending close order: {e}")
            raise
    
    def on_sl_update(self, trade_id: str, new_sl: float) -> None:
        """
        Called when stop loss should be updated.
        Sends modify order command to the platform.
        
        Args:
            trade_id: The trade ID to modify
            new_sl: The new stop loss price
        """
        try:
            self._gateway.send_modify_order(trade_id=trade_id, stop_loss=new_sl)
            self.logger.info(f"Sent SL update for trade {trade_id}: new_sl={new_sl}")
            
        except Exception as e:
            self.logger.error(f"Error sending modify order: {e}")
            raise


def create_zmq_executor(
    gateway: TradingGateway,
    *,
    risk_usd: Optional[float] = None,
    risk_pct: Optional[float] = None,
    logger: Optional[ILogger] = None,
) -> ZMQTradeExecutor:
    """
    Convenience factory function to create a ZMQTradeExecutor.
    
    Usage:
        from src.gateway import TradingGateway, create_zmq_executor
        
        gateway = TradingGateway()
        gateway.start()
        
        executor = create_zmq_executor(gateway, risk_usd=500)
        
        trade_manager = TradeManager(
            ...,
            trade_executor=executor,
        )
    
    Args:
        gateway: The TradingGateway instance
        risk_usd: Fixed dollar risk per trade
        risk_pct: Percentage of account to risk per trade
        
    Returns:
        Configured ZMQTradeExecutor
    """
    return ZMQTradeExecutor(
        gateway=gateway,
        risk_usd=risk_usd,
        risk_pct=risk_pct,
        logger=logger,
    )
