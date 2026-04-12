"""
Universal Trading Gateway using ZeroMQ.

This module provides a fast, cross-platform communication layer between
Python trading strategies and various trading platforms (NinjaTrader, 
MetaTrader, cTrader, etc.).

Usage:
    from src.gateway import TradingGateway, create_zmq_executor
    
    gateway = TradingGateway(pair="NQ")
    gateway.start()
    
    # Use with trade manager
    executor = create_zmq_executor(gateway)
    trade_manager = TradeManager(..., trade_executor=executor)
"""

from .protocol import (
    MessageType,
    TradeCommand,
    MarketDataMessage,
    FillMessage,
    HeartbeatMessage,
    TestPingMessage,
    TestPongMessage,
    TestStartMessage,
    TestResultMessage,
)
from .gateway import TradingGateway, GatewayConfig
from .executor import ZMQTradeExecutor, create_zmq_executor
from .datasource import ZMQDataSource
from .integration import (
    create_live_components,
    create_gateway_only,
    HybridDataSource,
    get_platform_addresses,
)

__all__ = [
    # Protocol types
    "MessageType",
    "TradeCommand", 
    "MarketDataMessage",
    "FillMessage",
    "HeartbeatMessage",
    "TestPingMessage",
    "TestPongMessage",
    "TestStartMessage",
    "TestResultMessage",
    # Gateway
    "TradingGateway",
    "GatewayConfig",
    # Executor
    "ZMQTradeExecutor",
    "create_zmq_executor",
    # DataSource
    "ZMQDataSource",
    # Integration helpers
    "create_live_components",
    "create_gateway_only",
    "HybridDataSource",
    "get_platform_addresses",
]
