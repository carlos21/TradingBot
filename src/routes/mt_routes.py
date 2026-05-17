"""MetaTrader management HTTP routes."""
from __future__ import annotations

from flask import Flask, jsonify, request

from src.utils.app_logger import ILogger


def register_mt_routes(
    app: Flask,
    mt_service,
    logger: ILogger,
):
    @app.route("/api/mt/launch", methods=["POST"])
    def api_mt_launch():
        payload = request.get_json(silent=True) or {}
        exe_path = payload.get("exe_path", "")
        try:
            result = mt_service.launch_terminal(exe_path=exe_path or None)
            return jsonify(result), 200
        except Exception as e:
            logger.error(f"[MT API] Unexpected error launching terminal: {e}")
            return jsonify({"success": False, "message": f"Internal server error: {e}"}), 500
