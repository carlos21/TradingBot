"""TradingBot entry point.

This module is intentionally thin. It:
1. Loads configuration (env vars + CLI args)
2. Builds the application via ``AppBuilder``
3. Starts the server

All heavy lifting lives in ``src.config`` and ``app_factory``.
"""
from dotenv import load_dotenv
load_dotenv()

from src.config import (
    CompositeConfigLoader,
    EnvConfigLoader,
    CliConfigLoader,
    AppBuilder,
)


def main():
    loader = CompositeConfigLoader(
        EnvConfigLoader(),
        CliConfigLoader(),
    )
    config = loader.load()

    builder = AppBuilder(config)
    wiring, ds = builder.build()

    if config.mode == "live":
        print("[App] Starting ZeroMQ gateway...")
        ds.start()
        print("[App] ZeroMQ gateway started")

        print("[App] Starting LIVE mode server (threaded, debug=False)")
        try:
            wiring.socketio.run(
                wiring.app,
                host="0.0.0.0",
                port=5001,
                debug=False,
                use_reloader=False,
            )
        finally:
            print("[App] Stopping ZeroMQ gateway...")
            ds.stop()
            print("[App] ZeroMQ gateway stopped")
    else:
        wiring.socketio.run(
            wiring.app,
            host="0.0.0.0",
            port=5001,
            debug=True,
            use_reloader=True,
        )


if __name__ == "__main__":
    main()
