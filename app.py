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
    print(f"[{instance}] Starting TradingBot instance — pair={config.pair} mode={config.mode}")

    if config.mode == "live":
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
