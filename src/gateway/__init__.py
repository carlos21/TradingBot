"""
Universal Trading Gateway using ZeroMQ.

This module provides a fast, cross-platform communication layer between
Python trading strategies and various trading platforms (NinjaTrader,
MetaTrader, cTrader, etc.).

Usage:
    from src.gateway import TradingGateway, create_zmq_executor

    gateway = TradingGateway(logger, pair="MNQ")
    gateway.start()

    # Use with trade manager
    executor = create_zmq_executor(gateway)
    trade_manager = TradeManager(..., trade_executor=executor)
"""

from .datasource import ZMQDataSource
from .executor import MultiAccountExecutor, ZMQTradeExecutor, create_zmq_executor
from .gateway import GatewayConfig, TradingGateway
from .integration import (
    create_gateway_only,
    create_live_components,
    create_multi_account_live_components,
    get_platform_addresses,
)
from .protocol import (
    FillMessage,
    HeartbeatMessage,
    MarketDataMessage,
    MessageType,
    TestPingMessage,
    TestPongMessage,
    TestResultMessage,
    TestStartMessage,
    TradeCommand,
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
    "create_multi_account_live_components",
    "create_gateway_only",
    "get_platform_addresses",
]
