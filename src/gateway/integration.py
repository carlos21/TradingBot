"""
Integration module for connecting ZeroMQ gateway to app_factory.

This module provides a drop-in replacement for the HTTP-based
NinjaTrader integration, making migration seamless.

Usage:
    # Instead of:
    from src.data_sources.ninjatrader_datasource import NinjaTraderDataSource
    data_source = NinjaTraderDataSource(...)
    
    # Use:
    from src.gateway import create_live_components
    data_source, trade_executor = create_live_components(pair="NQ")
    
    # Then pass to app_factory:
    wiring = create_app(
        data_source=data_source,
        trade_executor=trade_executor,
        live_mode=True,
        ...
    )
    
    # After create_app, start the gateway:
    data_source.start()
"""

from typing import Tuple, Optional

from src.data_sources.combined_datasource import CombinedDataSource
from src.services.trade_executor import TradeExecutor
from src.utils.app_logger import ILogger, ConsoleLogger

from .gateway import TradingGateway, GatewayConfig
from .datasource import ZMQDataSource
from .executor import ZMQTradeExecutor


def create_live_components(
    pair: str,
    *,
    risk_usd: Optional[float] = None,
    risk_pct: Optional[float] = None,
    host: str = "127.0.0.1",
    market_port: int = 5555,
    command_port: int = 5556,
    query_port: int = 5557,
    heartbeat_port: int = 5558,
    logger: Optional[ILogger] = None,
) -> Tuple[ZMQDataSource, ZMQTradeExecutor]:
    """
    Create ZeroMQ-based data source and trade executor for live trading.
    
    This is the main entry point for using ZeroMQ with the existing app_factory.
    
    Args:
        pair: Trading pair symbol (e.g., "NQ", "MNQ")
        risk_usd: Fixed dollar risk per trade (optional)
        risk_pct: Percentage of account to risk per trade (optional)
        host: ZeroMQ host address (default: localhost)
        market_port: Port for market data (PUB/SUB)
        command_port: Port for trade commands (PUSH/PULL)
        query_port: Port for sync queries (REQ/REP)
        heartbeat_port: Port for heartbeats (PUB/SUB)
    
    Returns:
        Tuple of (data_source, trade_executor) ready for use with create_app()
        
    Example:
        >>> from src.gateway import create_live_components
        >>> from app_factory import create_app
        >>> 
        >>> # Create ZeroMQ components
        >>> data_source, executor = create_live_components(
        ...     pair="NQ",
        ...     risk_usd=500,
        ... )
        >>> 
        >>> # Create Flask app with ZeroMQ components
        >>> wiring = create_app(
        ...     pair="NQ",
        ...     data_source=data_source,
        ...     trade_executor=executor,
        ...     live_mode=True,
        ...     ...
        ... )
        >>> 
        >>> # Start receiving data
        >>> data_source.start()
        >>> 
        >>> # Run Flask
        >>> wiring.socketio.run(wiring.app, port=5001)
    """
    # Use provided logger or create default
    logger = logger or ConsoleLogger()
    
    # Create shared gateway configuration
    config = GatewayConfig(
        market_data_pub=f"tcp://{host}:{market_port}",
        command_pull=f"tcp://{host}:{command_port}",
        query_rep=f"tcp://{host}:{query_port}",
        heartbeat_pub=f"tcp://{host}:{heartbeat_port}",
        platform_connects=True,  # Python binds, platform connects
    )
    
    # Create gateway with logger
    gateway = TradingGateway(config=config, pair=pair, logger=logger)
    
    # Create data source that uses the gateway
    data_source = ZMQDataSource(gateway=gateway, pair=pair, logger=logger)
    
    # Create trade executor that uses the same gateway
    trade_executor = ZMQTradeExecutor(
        gateway=gateway,
        risk_usd=risk_usd,
        risk_pct=risk_pct,
        logger=logger,
    )
    
    logger.info(f"Created ZeroMQ live components for {pair}")
    logger.info(f"  Market data: tcp://{host}:{market_port}")
    logger.info(f"  Commands: tcp://{host}:{command_port}")
    
    return data_source, trade_executor


def create_gateway_only(
    pair: str,
    host: str = "127.0.0.1",
    market_port: int = 5555,
    command_port: int = 5556,
    query_port: int = 5557,
    heartbeat_port: int = 5558,
) -> TradingGateway:
    """
    Create just the ZeroMQ gateway for advanced use cases.
    
    Use this if you want to manually wire the data source and executor,
    or if you need to share a gateway between multiple components.
    
    Args:
        pair: Trading pair symbol
        host: ZeroMQ host address
        market_port: Port for market data
        command_port: Port for trade commands
        query_port: Port for sync queries
        heartbeat_port: Port for heartbeats
        
    Returns:
        Configured TradingGateway instance
    """
    config = GatewayConfig(
        market_data_pub=f"tcp://{host}:{market_port}",
        command_pull=f"tcp://{host}:{command_port}",
        query_rep=f"tcp://{host}:{query_port}",
        heartbeat_pub=f"tcp://{host}:{heartbeat_port}",
        platform_connects=True,
    )
    
    return TradingGateway(config=config, pair=pair)


class HybridDataSource(CombinedDataSource):
    """
    Hybrid data source that can use both HTTP and ZeroMQ.
    
    This is useful for gradual migration - you can switch between
    protocols without changing the rest of your code.
    
    Usage:
        # ZeroMQ mode
        zmq_ds, executor = create_live_components(pair="NQ")
        
        # HTTP mode (existing)
        http_ds = NinjaTraderDataSource(...)
        
        # Hybrid - uses ZeroMQ if available, falls back to HTTP
        hybrid = HybridDataSource(primary=zmq_ds, fallback=http_ds)
    """
    
    def __init__(
        self,
        primary: CombinedDataSource,
        fallback: CombinedDataSource,
    ):
        self._primary = primary
        self._fallback = fallback
        self._use_primary = True
    
    @property
    def active_source(self) -> CombinedDataSource:
        """Get the currently active data source."""
        return self._primary if self._use_primary else self._fallback
    
    def switch_to_primary(self) -> None:
        """Switch to primary (ZeroMQ) data source."""
        self._use_primary = True
        logger.info("Switched to primary (ZeroMQ) data source")
    
    def switch_to_fallback(self) -> None:
        """Switch to fallback (HTTP) data source."""
        self._use_primary = False
        logger.info("Switched to fallback (HTTP) data source")
    
    # CombinedDataSource interface implementation
    def load_historical_bars(self, timeframe: str = "1m", start_time: int = None):
        return self.active_source.load_historical_bars(timeframe, start_time)
    
    def subscribe(self, callback, from_time: int = 0):
        return self.active_source.subscribe(callback, from_time)
    
    def pause(self):
        return self.active_source.pause()
    
    def shutdown(self):
        self._primary.shutdown()
        self._fallback.shutdown()


def get_platform_addresses(
    host: str = "127.0.0.1",
    market_port: int = 5555,
    command_port: int = 5556,
    query_port: int = 5557,
    heartbeat_port: int = 5558,
) -> dict:
    """
    Get the ZeroMQ addresses that platforms should connect to.
    
    Use this to generate configuration for NinjaTrader, MetaTrader, etc.
    
    Returns:
        Dictionary of address strings for each socket type
    """
    return {
        "market_data": f"tcp://{host}:{market_port}",
        "commands": f"tcp://{host}:{command_port}",
        "queries": f"tcp://{host}:{query_port}",
        "heartbeat": f"tcp://{host}:{heartbeat_port}",
    }
