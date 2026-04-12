#!/usr/bin/env python3
"""
Example: Live trading with ZeroMQ.

This script demonstrates how to use the ZeroMQ gateway for live trading
with NinjaTrader or other platforms.

Prerequisites:
    1. Install dependencies: poetry install
    2. Build and run the NinjaTrader ZMQ connector
    3. Run this script

Usage:
    python examples/zmq_live_trading.py

The script will:
    1. Start the ZeroMQ gateway (listening on ports 5555-5558)
    2. Wait for NinjaTrader to connect
    3. Receive historical bars
    4. Start live trading when history is complete
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gateway import create_live_components
from src.utils.app_logger import FileAndConsoleLogger
from app_factory import create_app, Repositories
from src.repositories.trades_repository import TradeRepository
from src.repositories.lines_repository import LineRepository
from src.strategies.strategy_config import StrategyNumbers


def main():
    # Configuration
    PAIR = "NQ"
    RISK_USD = 500  # $500 risk per trade
    VERBOSE = False  # Set True for debug logging of all ZMQ messages
    
    # Create logger (file + console for live mode)
    logger = FileAndConsoleLogger(log_dir="logs")
    
    logger.info("=" * 60)
    logger.info("TradingBot - ZeroMQ Live Trading Example")
    logger.info("=" * 60)
    
    # Create ZeroMQ live components
    logger.info("[1] Creating ZeroMQ components...")
    logger.info(f"    Verbose logging: {'ON' if VERBOSE else 'OFF'} (set VERBOSE=True for debug)")
    data_source, trade_executor = create_live_components(
        PAIR,
        logger,  # Required logger - all components share this instance
        risk_usd=RISK_USD,
        host="127.0.0.1",
        market_port=5555,
        command_port=5556,
        query_port=5557,
        heartbeat_port=5558,
    )
    
    # Create repositories
    logger.info("[2] Setting up repositories...")
    db_path = os.path.join(os.path.dirname(__file__), "..", "database.db")
    trades_repo = TradeRepository(db_path=db_path)
    lines_repo = LineRepository(db_path=db_path)
    repos = Repositories(lines=lines_repo, trades=trades_repo)
    
    # Strategy numbers
    numbers = StrategyNumbers(
        min_stop_loss=80,
        max_bounce=20,
        extra_sl_space=20,
        fixed_stop_loss=None,
        max_stop_loss=150,
        sl_levels=[80, 100, 120, 140, 150],
        sl_level_tolerance=5,
        min_cross_depth=3,
        rr_ratio=5.0,
        point_value=20.0,
        account_balance=100000,
        risk_per_trade=RISK_USD,
        risk_pct_per_trade=None,
    )
    
    # Create Flask app with ZeroMQ components
    logger.info("[3] Creating Flask app...")
    wiring = create_app(
        pair=PAIR,
        data_source=data_source,
        repos=repos,
        numbers=numbers,
        live_mode=True,
        trade_executor=trade_executor,
        timeframes=["1m", "5m", "15m"],
        bootstrap_existing_lines=True,
    )
    
    # Start the ZeroMQ data source
    logger.info("[4] Starting ZeroMQ gateway...")
    data_source.start()
    
    logger.info("=" * 60)
    logger.info("Waiting for NinjaTrader connection...")
    logger.info("=" * 60)
    logger.info("")
    logger.info("In NinjaTrader:")
    logger.info("  1. Open Control Center")
    logger.info("  2. Click 'New' > 'TradingBot ZMQ Connector'")
    logger.info("  3. Click 'Connect'")
    logger.info("")
    logger.info("The system will automatically:")
    logger.info("  - Receive historical bars")
    logger.info("  - Start live trading")
    logger.info("")
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 60)
    
    try:
        # Run Flask-SocketIO
        wiring.socketio.run(
            wiring.app,
            host="0.0.0.0",
            port=5001,
            debug=False,
            use_reloader=False,
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        data_source.stop()
        logger.info("ZeroMQ gateway stopped")
        logger.close()


if __name__ == "__main__":
    main()
