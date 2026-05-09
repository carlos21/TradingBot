"""Streaming lifecycle HTTP routes."""
from __future__ import annotations

import threading
import time

from flask import Flask, jsonify, request

from src.infrastructure.gateway.datasource import ZMQDataSource
from src.utils.app_logger import ILogger


def register_stream_routes(
    app: Flask,
    data_source,
    nt_service,
    settings_service,
    socketio,
    logger: ILogger,
):
    """Register stream start/stop/status routes.

    Args:
        app: Flask application
        data_source: The live data source (expected to be ZMQDataSource in live mode)
        nt_service: NtManagerService for launching NinjaTrader
        settings_service: SettingsService for retrieving decrypted NT credentials
        socketio: SocketIO instance for emitting events
        logger: Logger instance
    """

    def _maybe_launch_nt_after_delay():
        """Background: wait a few seconds for NT to connect on its own,
        then launch auto-login only if it hasn't."""
        time.sleep(4)
        if not isinstance(data_source, ZMQDataSource):
            return
        gateway = data_source.gateway
        if gateway and gateway.is_connected:
            logger.info("[Stream] NinjaTrader connected on its own, skipping auto-login.")
            return
        try:
            settings = settings_service.get_full_settings()
            creds = settings.get("credentials", {})
            username = creds.get("username", "")
            password = creds.get("password", "")

            if username and password:
                logger.info(f"[Stream] NinjaTrader not detected after 4s, launching auto-login for {username}...")
                result = nt_service.open_nt_and_login(username, password)
                if not result.get("success"):
                    logger.warning(f"[Stream] NT launch warning: {result.get('message')}")
            else:
                logger.warning("[Stream] No NT credentials configured. Please connect NinjaTrader manually.")
        except Exception as e:
            logger.error(f"[Stream] Failed to launch NinjaTrader: {e}")

    @app.route("/api/stream/status", methods=["GET"])
    def api_stream_status():
        if not isinstance(data_source, ZMQDataSource):
            return jsonify({
                "live_mode": False,
                "gateway_running": False,
                "platform_connected": False,
                "platform_info": None,
            }), 200

        gateway = data_source.gateway
        return jsonify({
            "live_mode": True,
            "gateway_running": gateway.is_running if gateway else False,
            "platform_connected": gateway.is_connected if gateway else False,
            "platform_info": gateway.platform_info if gateway else None,
        }), 200

    @app.route("/api/stream/start", methods=["POST"])
    def api_stream_start():
        if not isinstance(data_source, ZMQDataSource):
            return jsonify({
                "status": "error",
                "message": "Streaming is only available in live mode with ZMQ data source",
            }), 400

        gateway = data_source.gateway
        if gateway and gateway.is_connected:
            return jsonify({
                "status": "already_connected",
                "message": "NinjaTrader is already connected",
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

        # Always offer to launch NinjaTrader if it's not connected yet
        thread = threading.Thread(target=_maybe_launch_nt_after_delay, daemon=True)
        thread.start()

        return jsonify({
            "status": "starting",
            "message": "ZeroMQ gateway started; waiting for NinjaTrader...",
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
