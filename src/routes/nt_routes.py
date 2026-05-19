"""NinjaTrader management HTTP routes."""
from __future__ import annotations

from flask import Flask, jsonify, request

from src.utils.app_logger import ILogger


def register_nt_routes(
    app: Flask,
    nt_service,
    deploy_service,
    _logger: ILogger,
):
    @app.route("/api/nt/install-netmq", methods=["POST"])
    def api_nt_install_netmq():
        """Legacy endpoint — now delegates to deploy."""
        result = deploy_service.deploy_ninjatrader()
        return jsonify(result), 200

    @app.route("/api/nt/deploy", methods=["POST"])
    def api_nt_deploy():
        payload = request.get_json(silent=True) or {}
        try:
            result = deploy_service.deploy_ninjatrader(
                target_dir=payload.get("target_dir") or None,
            )
            return jsonify(result), 200
        except Exception as e:
            _logger.error(f"[NT API] Unexpected error deploying files: {e}")
            return jsonify({"success": False, "message": f"Internal server error: {e}"}), 500

    @app.route("/api/nt/open", methods=["POST"])
    def api_nt_open():
        payload = request.get_json(silent=True) or {}
        username = payload.get("username", "")
        password = payload.get("password", "")
        result = nt_service.open_nt_and_login(username, password)
        return jsonify(result), 200
