"""
Integration module for connecting ZeroMQ gateway to app_factory.

This module provides the live trading entry point using ZeroMQ.

Usage:
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
    logger: ILogger,
    *,
    risk_usd: Optional[float] = None,
    risk_pct: Optional[float] = None,
    account: Optional[str] = None,
    host: str = "127.0.0.1",
    market_port: int = 5555,
    command_port: int = 5556,
    query_port: int = 5557,
    heartbeat_port: int = 5558,
) -> Tuple[ZMQDataSource, ZMQTradeExecutor]:
    """
    Create ZeroMQ-based data source and trade executor for live trading.
    
    This is the main entry point for using ZeroMQ with the existing app_factory.
    
    Args:
        pair: Trading pair symbol (e.g., "NQ", "MNQ")
        logger: Logger instance (required)
        risk_usd: Fixed dollar risk per trade (optional)
        risk_pct: Percentage of account to risk per trade (optional)
        account: Trading account name to use (optional, uses first available if not specified)
        host: ZeroMQ host address (default: localhost)
        market_port: Port for market data (PUB/SUB)
        command_port: Port for trade commands (PUSH/PULL)
        query_port: Port for sync queries (REQ/REP)
        heartbeat_port: Port for heartbeats (PUB/SUB)
    
    Returns:
        Tuple of (data_source, trade_executor) ready for use with create_app()
        
    Example:
        >>> from src.gateway import create_live_components
        >>> from src.utils.app_logger import FileAndConsoleLogger
        >>> from app_factory import create_app
        >>> 
        >>> # Create logger
        >>> logger = FileAndConsoleLogger(log_dir="logs")
        >>> 
        >>> # Create ZeroMQ components
        >>> data_source, executor = create_live_components(
        ...     pair="NQ",
        ...     logger=logger,
        ...     risk_usd=500,
        ...     account="MyAccount",
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
    
    # Create shared gateway configuration
    config = GatewayConfig(
        market_data_pub=f"tcp://{host}:{market_port}",
        command_pull=f"tcp://{host}:{command_port}",
        query_rep=f"tcp://{host}:{query_port}",
        heartbeat_pub=f"tcp://{host}:{heartbeat_port}",
        platform_connects=True,  # Python binds, platform connects
    )
    
    # Create gateway with logger (logger is required first param)
    gateway = TradingGateway(logger, config=config, pair=pair)
    
    # Store account name for config queries from NinjaTrader
    gateway._account_name = account
    
    # Create data source that uses the gateway (logger is required first param)
    data_source = ZMQDataSource(logger, gateway=gateway, pair=pair)
    
    # Create trade executor that uses the same gateway (logger is required)
    trade_executor = ZMQTradeExecutor(
        gateway,
        logger,
        risk_usd=risk_usd,
        risk_pct=risk_pct,
    )
    
    logger.info(f"Created ZeroMQ live components for {pair}")
    logger.info(f"  Market data: tcp://{host}:{market_port}")
    logger.info(f"  Commands: tcp://{host}:{command_port}")
    if account:
        logger.info(f"  Account: {account}")
    
    return data_source, trade_executor


def create_gateway_only(
    pair: str,
    logger: ILogger,
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
        logger: Logger instance (required)
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
    
    return TradingGateway(logger, config=config, pair=pair)


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
