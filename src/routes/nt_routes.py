"""NinjaTrader management HTTP routes."""
from __future__ import annotations

from flask import Flask, jsonify, request

from src.utils.app_logger import ILogger


def register_nt_routes(
    app: Flask,
    nt_service,
    _logger: ILogger,
):
    @app.route("/api/nt/install-netmq", methods=["POST"])
    def api_nt_install_netmq():
        result = nt_service.install_netmq()
        return jsonify(result), 200

    @app.route("/api/nt/open", methods=["POST"])
    def api_nt_open():
        payload = request.get_json(silent=True) or {}
        username = payload.get("username", "")
        password = payload.get("password", "")
        result = nt_service.open_nt_and_login(username, password)
        return jsonify(result), 200
