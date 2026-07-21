"""Settings HTTP routes."""
from __future__ import annotations

from flask import Flask

from src.utils.app_logger import ILogger


def register_settings_routes(
    app: Flask,
    settings_controller,
    _logger: ILogger,
):
    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        return settings_controller.get_settings()

    @app.route("/api/settings", methods=["POST"])
    def api_save_settings():
        return settings_controller.save_settings()

    @app.route("/api/settings/credentials", methods=["POST"])
    def api_save_credentials():
        return settings_controller.save_credentials()

    @app.route("/api/accounts", methods=["GET"])
    def api_get_accounts():
        return settings_controller.get_accounts()

    @app.route("/api/accounts", methods=["POST"])
    def api_save_account():
        return settings_controller.save_account()

    @app.route("/api/accounts/<string:name>", methods=["DELETE"])
    def api_delete_account(name: str):
        return settings_controller.delete_account(name)
