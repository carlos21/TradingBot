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
    print("=" * 60)
    print("TradingBot - ZeroMQ Live Trading Example")
    print("=" * 60)
    
    # Configuration
    PAIR = "NQ"
    RISK_USD = 500  # $500 risk per trade
    
    # Create logger (file + console for live mode)
    print("\n[1] Creating logger...")
    logger = FileAndConsoleLogger(log_dir="logs")
    
    # Create ZeroMQ live components
    print("\n[2] Creating ZeroMQ components...")
    data_source, trade_executor = create_live_components(
        pair=PAIR,
        risk_usd=RISK_USD,
        host="127.0.0.1",
        market_port=5555,
        command_port=5556,
        query_port=5557,
        heartbeat_port=5558,
        logger=logger,  # Pass custom logger
    )
    
    # Create repositories
    print("\n[3] Setting up repositories...")
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
    print("\n[4] Creating Flask app...")
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
    print("\n[5] Starting ZeroMQ gateway...")
    data_source.start()
    
    print("\n" + "=" * 60)
    print("Waiting for NinjaTrader connection...")
    print("=" * 60)
    print("\nIn NinjaTrader:")
    print("  1. Open Control Center")
    print("  2. Click 'New' > 'TradingBot ZMQ Connector'")
    print("  3. Click 'Connect'")
    print("\nThe system will automatically:")
    print("  - Receive historical bars")
    print("  - Start live trading")
    print("\nPress Ctrl+C to stop")
    print("=" * 60 + "\n")
    
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
        print("\n\nShutting down...")
    finally:
        data_source.stop()
        print("ZeroMQ gateway stopped")


if __name__ == "__main__":
    main()
