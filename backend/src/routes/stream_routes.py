"""Streaming lifecycle HTTP routes."""
from __future__ import annotations

import threading

from flask import Flask, jsonify

from src.application.ports import PlatformLifecycleService
from src.infrastructure.gateway.datasource import DataSourceState, ZMQDataSource
from src.utils.app_logger import ILogger


def register_stream_routes(
    app: Flask,
    data_source,
    platform_lifecycle: PlatformLifecycleService,
    socketio,
    logger: ILogger,
    coordinator=None,
):
    """Register stream start/stop/status routes.

    Args:
        app: Flask application
        data_source: The live data source (expected to be ZMQDataSource in live mode)
        platform_lifecycle: Platform-specific lifecycle service for validation and auto-launch
        socketio: SocketIO instance for emitting events
        logger: Logger instance
        coordinator: Optional StreamCoordinator for per-instrument streaming
    """

    @app.route("/api/stream/status", methods=["GET"])
    def api_stream_status():
        if not isinstance(data_source, ZMQDataSource):
            return jsonify({
                "live_mode": False,
                "gateway_running": False,
                "platform_connected": False,
                "platform_info": None,
                "has_accounts": True,  # Not applicable in backtest mode
            }), 200

        gateway = data_source.gateway
        return jsonify({
            "live_mode": True,
            "gateway_running": gateway.is_running if gateway else False,
            "platform_connected": gateway.is_connected if gateway else False,
            "platform_info": gateway.platform_info if gateway else None,
            "has_accounts": platform_lifecycle.has_accounts_configured(),
        }), 200

    @app.route("/api/stream/start", methods=["POST"])
    def api_stream_start():
        if not isinstance(data_source, ZMQDataSource):
            return jsonify({
                "status": "error",
                "message": "Streaming is only available in live mode with ZMQ data source",
            }), 400

        ok, err = platform_lifecycle.validate_before_start(data_source)
        if not ok:
            return jsonify({"status": "error", "message": err}), 400

        gateway = data_source.gateway
        if gateway and gateway.is_connected:
            # Recover a datasource stuck in CONNECTED (platform connected but no
            # history ever arrived): kick a refresh. Self-guarded — request_refresh
            # is a no-op while refreshing or disconnected, and we skip it entirely
            # when the stream is healthy.
            if data_source.state != DataSourceState.STREAMING:
                data_source.request_refresh()
            platform_label = "Platform"
            return jsonify({
                "status": "already_connected",
                "message": f"{platform_label} is already connected",
            }), 200

        # Start gateway only if it's not already running
        if not gateway or not gateway.is_running:
            try:
                logger.info("[Stream] Starting ZeroMQ gateway...")
                data_source.start()
                socketio.emit("gateway_started")
                logger.info("[Stream] ZeroMQ gateway started, sockets bound")
            except Exception as e:
                logger.error(f"[Stream] Failed to start ZeroMQ gateway: {e}")
                return jsonify({
                    "status": "error",
                    "message": f"Failed to start ZeroMQ gateway: {e}",
                }), 500

        # Spawn background thread to auto-launch platform if it doesn't connect on its own
        thread = threading.Thread(
            target=platform_lifecycle.maybe_launch_after_delay,
            args=(data_source,),
            daemon=True,
        )
        thread.start()

        return jsonify({
            "status": "starting",
            "message": "ZeroMQ gateway started; waiting for platform...",
        }), 200

    @app.route("/api/stream/stop", methods=["POST"])
    def api_stream_stop():
        if not isinstance(data_source, ZMQDataSource):
            return jsonify({
                "status": "error",
                "message": "Streaming is only available in live mode",
            }), 400

        try:
            logger.info("[Stream] Stopping ZeroMQ gateway...")
            data_source.stop()
            if coordinator is not None:
                coordinator.stop_all()
            socketio.emit("gateway_stopped")
            logger.info("[Stream] ZeroMQ gateway stopped")
        except Exception as e:
            logger.error(f"[Stream] Failed to stop ZeroMQ gateway: {e}")
            return jsonify({
                "status": "error",
                "message": f"Failed to stop ZeroMQ gateway: {e}",
            }), 500

        return jsonify({
            "status": "stopped",
            "message": "ZeroMQ gateway stopped",
        }), 200
