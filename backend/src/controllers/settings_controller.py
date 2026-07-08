"""Controller for settings API endpoints."""
from __future__ import annotations

import logging

from flask import abort, jsonify, request

from src.services.settings_service import SettingsService


logger = logging.getLogger(__name__)


class SettingsController:
    """Controller for DB-backed settings operations."""

    def __init__(self, settings_service: SettingsService):
        self._svc = settings_service

    def get_settings(self):
        return jsonify(self._svc.get_full_settings()), 200

    def save_settings(self):
        payload = request.get_json(silent=True) or {}
        try:
            self._svc.save_full_settings(payload)
        except Exception as exc:
            logger.exception("Failed to save settings: %s", exc)
            abort(500, f"Failed to save settings: {exc}")
        return jsonify({"success": True}), 200

    def get_accounts(self):
        data = self._svc.get_full_settings()
        return jsonify(data.get("accounts", [])), 200

    def save_account(self):
        payload = request.get_json(silent=True) or {}
        try:
            self._svc.save_account(payload)
        except ValueError as exc:
            abort(400, str(exc))
        except Exception as exc:
            abort(500, f"Failed to save account: {exc}")
        return jsonify({"name": payload.get("name")}), 200

    def delete_account(self, name: str):
        try:
            self._svc.delete_account(name)
        except Exception as exc:
            abort(500, f"Failed to delete account: {exc}")
        return "", 204
