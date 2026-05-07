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
    AppBuilder,
)


def main():
    loader = CompositeConfigLoader(
        EnvConfigLoader(),
        CliConfigLoader(),
        DbConfigLoader(db_path=os.environ.get("DB_PATH", "sqlite:///./database.db")),
    )
    config = loader.load()

    builder = AppBuilder(config)
    wiring, ds = builder.build()

    instance = config.instance_name
    print(f"[{instance}] Starting Liquid instance — pair={config.pair} mode={config.mode}")

    # Register signal handlers for graceful shutdown
    _shutdown_triggered = False

    def _signal_handler(signum, frame):
        nonlocal _shutdown_triggered
        if _shutdown_triggered:
            return
        _shutdown_triggered = True
        sig_name = signal.Signals(signum).name
        print(f"\n[{instance}] Received {sig_name}, shutting down gracefully...")
        if config.mode == "live" and ds is not None:
            ds.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if config.mode == "live":
        if not getattr(config, "nt_accounts", None):
            print(f"[{instance}] WARNING: No NT accounts configured. Live streaming will be unavailable until accounts are added.")
        else:
            print(f"[{instance}] Starting ZeroMQ gateway...")
            ds.start()
            print(f"[{instance}] ZeroMQ gateway started")

        print(f"[{instance}] Starting LIVE mode server on port {config.flask_port} (threaded, debug=False)")
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
                print(f"[{instance}] Stopping ZeroMQ gateway...")
                ds.stop()
                print(f"[{instance}] ZeroMQ gateway stopped")
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
