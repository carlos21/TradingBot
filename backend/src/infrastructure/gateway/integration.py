"""
Integration module for connecting ZeroMQ gateway to app_factory.

This module provides the live trading entry point using ZeroMQ.

Usage:
    from src.infrastructure.gateway import create_live_components
    data_source, trade_executor = create_live_components(logger)

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


from src.config.models import DEFAULT_HISTORY_HOURS
from src.infrastructure.market_closure_filter import MarketClosureFilter
from src.notifier import Notifier
from src.utils.app_logger import ILogger

from .datasource import ZMQDataSource
from .executor import MultiAccountExecutor, ZMQTradeExecutor
from .gateway import GatewayConfig, TradingGateway


def create_live_components(
    logger: ILogger,
    *,
    risk_usd: float | None = None,
    risk_pct: float | None = None,
    account_names: list[str] | None = None,
    host: str = "127.0.0.1",
    market_port: int = 5555,
    command_port: int = 5556,
    query_port: int = 5557,
    heartbeat_port: int = 5558,
    history_hours: int = DEFAULT_HISTORY_HOURS,
    notifier: Notifier | None = None,
) -> tuple[ZMQDataSource, ZMQTradeExecutor]:
    """
    Create ZeroMQ-based data source and trade executor for live trading.

    This is the main entry point for using ZeroMQ with the existing app_factory.
    There is no configured default instrument: the platform subscribes exactly
    the instruments users select in the UI.

    Args:
        logger: Logger instance (required)
        risk_usd: Fixed dollar risk per trade (optional)
        risk_pct: Percentage of account to risk per trade (optional)
        account_names: List of trading account names for config queries
        host: ZeroMQ host address (default: localhost)
        market_port: Port for market data (PUB/SUB)
        command_port: Port for trade commands (PUSH/PULL)
        query_port: Port for sync queries (REQ/REP)
        heartbeat_port: Port for heartbeats (PUB/SUB)

    Returns:
        Tuple of (data_source, trade_executor) ready for use with create_app()
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
    gateway = TradingGateway(logger, config=config)

    # Store account names for config queries from NinjaTrader
    gateway.set_account_names(account_names or [])

    # Create data source that uses the gateway (logger is required first param)
    data_source = ZMQDataSource(
        logger,
        gateway=gateway,
        history_hours=history_hours,
        notifier=notifier,
        market_filter=MarketClosureFilter(),
    )

    # Create trade executor that uses the same gateway (logger is required)
    trade_executor = ZMQTradeExecutor(
        gateway,
        logger,
        risk_usd=risk_usd,
        risk_pct=risk_pct,
    )

    logger.info("Created ZeroMQ live components")
    logger.info(f"  Market data: tcp://{host}:{market_port}")
    logger.info(f"  Commands: tcp://{host}:{command_port}")
    if account_names:
        logger.info(f"  Accounts: {', '.join(account_names)}")

    return data_source, trade_executor


def create_multi_account_live_components(
    logger: ILogger,
    *,
    account_configs,
    risk_usd: float | None = None,
    risk_pct: float | None = None,
    host: str = "127.0.0.1",
    market_port: int = 5555,
    command_port: int = 5556,
    query_port: int = 5557,
    heartbeat_port: int = 5558,
    accounts_repo=None,
    history_hours: int = DEFAULT_HISTORY_HOURS,
    notifier: Notifier | None = None,
) -> tuple[ZMQDataSource, MultiAccountExecutor]:
    """
    Create ZeroMQ components for multi-account live trading.

    Uses a single gateway; the MultiAccountExecutor expands each signal
    into per-account trades and sends tagged commands to the single
    NinjaTrader connector.

    Args:
        logger: Logger instance (required)
        account_configs: List of AccountConfig objects (name + per-account risk)
        risk_usd: Default fixed dollar risk (fallback when account risk not set)
        risk_pct: Default percentage risk (fallback when account risk not set)
        host: ZeroMQ host address
        market_port: Port for market data
        command_port: Port for trade commands
        query_port: Port for sync queries
        heartbeat_port: Port for heartbeats

    Returns:
        Tuple of (data_source, multi_account_executor)
    """

    account_names = [a.name for a in account_configs]
    data_source, gateway_executor = create_live_components(
        logger=logger,
        risk_usd=risk_usd,
        risk_pct=risk_pct,
        account_names=account_names,
        host=host,
        market_port=market_port,
        command_port=command_port,
        query_port=query_port,
        heartbeat_port=heartbeat_port,
        history_hours=history_hours,
    )

    # MultiAccountExecutor will be wired with TradeManager inside app_factory
    # We return a placeholder that gets its trade_manager injected later.
    # For now, create it without trade_manager; app_factory will set it.
    placeholder = MultiAccountExecutor(
        trade_manager=None,
        account_configs=account_configs,
        gateway_executor=gateway_executor,
        logger=logger,
        accounts_repo=accounts_repo,
    )

    logger.info("Created multi-account live components")
    for acct in account_configs:
        risk_info = f"risk=${acct.risk_usd}" if acct.risk_usd is not None else (f"risk_pct={acct.risk_pct}%" if acct.risk_pct is not None else "default")
        logger.info(f"  Account: {acct.name} ({risk_info})")

    return data_source, placeholder


def create_gateway_only(
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

    return TradingGateway(logger, config=config)


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
