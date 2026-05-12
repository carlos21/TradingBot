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
    app_config=None,
):
    platform_type = getattr(app_config, "platform_type", "ninjatrader") if app_config else "ninjatrader"
    platform_label = "NinjaTrader" if platform_type == "ninjatrader" else "MetaTrader"
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
        """Background: wait a few seconds for platform to connect on its own,
        then launch auto-login only if it hasn't (NinjaTrader only)."""
        if platform_type != "ninjatrader":
            return
        time.sleep(4)
        if not isinstance(data_source, ZMQDataSource):
            return
        gateway = data_source.gateway
        if gateway and gateway.is_connected:
            logger.info(f"[Stream] {platform_label} connected on its own, skipping auto-login.")
            return
        try:
            settings = settings_service.get_full_settings()
            creds = settings.get("credentials", {})
            username = creds.get("username", "")
            password = creds.get("password", "")

            if username and password:
                logger.info(f"[Stream] {platform_label} not detected after 4s, launching auto-login for {username}...")
                result = nt_service.open_nt_and_login(username, password)
                if not result.get("success"):
                    logger.warning(f"[Stream] NT launch warning: {result.get('message')}")
            else:
                logger.warning(f"[Stream] No NT credentials configured. Please connect {platform_label} manually.")
        except Exception as e:
            logger.error(f"[Stream] Failed to launch {platform_label}: {e}")

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
        has_accounts = True
        if platform_type == "ninjatrader":
            try:
                accounts_repo = getattr(settings_service, '_accounts', None)
                if accounts_repo is not None:
                    has_accounts = bool(accounts_repo.list_accounts())
            except Exception:
                has_accounts = False

        return jsonify({
            "live_mode": True,
            "gateway_running": gateway.is_running if gateway else False,
            "platform_connected": gateway.is_connected if gateway else False,
            "platform_info": gateway.platform_info if gateway else None,
            "has_accounts": has_accounts,
        }), 200

    @app.route("/api/stream/start", methods=["POST"])
    def api_stream_start():
        if not isinstance(data_source, ZMQDataSource):
            return jsonify({
                "status": "error",
                "message": "Streaming is only available in live mode with ZMQ data source",
            }), 400

        # Dynamically check accounts from DB (for ninjatrader)
        if platform_type == "ninjatrader":
            try:
                accounts_repo = getattr(settings_service, '_accounts', None)
                if accounts_repo is not None:
                    account_list = accounts_repo.list_accounts()
                    if not account_list:
                        return jsonify({
                            "status": "error",
                            "message": "No NinjaTrader accounts configured. Go to Admin → Settings and add at least one account before starting streaming.",
                        }), 400
            except Exception as e:
                logger.error(f"[Stream] Failed to check accounts: {e}")

        gateway = data_source.gateway
        if gateway and gateway.is_connected:
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

        # Always offer to launch platform auto-login if it's not connected yet (NT only)
        if platform_type == "ninjatrader":
            thread = threading.Thread(target=_maybe_launch_nt_after_delay, daemon=True)
            thread.start()

        return jsonify({
            "status": "starting",
            "message": f"ZeroMQ gateway started; waiting for {platform_label}...",
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
