"""TradingBot entry point.

This module is intentionally thin. It:
1. Loads configuration (env vars + CLI args)
2. Builds the application via ``AppBuilder``
3. Starts the server

All heavy lifting lives in ``src.config`` and ``app_factory``.
"""
from dotenv import load_dotenv
load_dotenv()

import os
import signal
import sys

from src.config import (
    CompositeConfigLoader,
    DbConfigLoader,
    EnvConfigLoader,
    CliConfigLoader,
)
from src.config.builder import AppBuilder
from src.utils.app_logger import configure_logging, log_timestamp

# Ensure all standard-library loggers emit datetimes in a consistent format.
configure_logging()


def main():
    loader = CompositeConfigLoader(
        EnvConfigLoader(),
        CliConfigLoader(),
        DbConfigLoader(
            db_path=os.environ.get(
                "DATABASE_URL",
                os.environ.get("DB_PATH", "sqlite:///./database.db"),
            )
        ),
    )
    config = loader.load()

    builder = AppBuilder(config)
    wiring, ds = builder.build()

    instance = config.instance_name
    app_logger = wiring.logger
    app_logger.info(
        f"[{instance}] Starting Liquid instance — mode={config.mode}"
    )

    # Register signal handlers for graceful shutdown
    _shutdown_triggered = False

    def _signal_handler(signum, frame):
        nonlocal _shutdown_triggered
        if _shutdown_triggered:
            return
        _shutdown_triggered = True
        sig_name = signal.Signals(signum).name
        print(
            f"\n{log_timestamp()} [{instance}] Received {sig_name}, shutting down gracefully...",
            flush=True,
        )
        if config.mode == "live" and ds is not None:
            ds.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if config.mode == "live":
        platform_label = "NinjaTrader" if config.platform_type == "ninjatrader" else "MetaTrader"
        if config.platform_type == "ninjatrader" and not config.nt_accounts:
            app_logger.warning(
                f"[{instance}] No NT accounts configured. Live streaming will be unavailable until accounts are added."
            )
        else:
            app_logger.info(f"[{instance}] Starting ZeroMQ gateway...")
            ds.start()
            app_logger.info(f"[{instance}] ZeroMQ gateway started")

        app_logger.info(
            f"[{instance}] Starting LIVE mode server on port {config.flask_port} (threaded, debug=False)"
        )
        try:
            wiring.socketio.run(
                wiring.app,
                host="0.0.0.0",
                port=config.flask_port,
                debug=False,
                use_reloader=False,
                allow_unsafe_werkzeug=True,
            )
        finally:
            if ds is not None:
                app_logger.info(f"[{instance}] Stopping ZeroMQ gateway...")
                ds.stop()
                app_logger.info(f"[{instance}] ZeroMQ gateway stopped")
    else:
        wiring.socketio.run(
            wiring.app,
            host="0.0.0.0",
            port=config.flask_port,
            debug=True,
            use_reloader=True,
        )


if __name__ == "__main__":
    main()
