"""Controller for settings API endpoints."""
from __future__ import annotations

from flask import abort, jsonify, request

from src.services.settings_service import SettingsService


class SettingsController:
    """Controller for DB-backed settings operations."""

    def __init__(self, settings_service: SettingsService):
        self._svc = settings_service

    def get_settings(self):
        return jsonify(self._svc.get_full_settings()), 200

    def save_settings(self):
        payload = request.get_json(silent=True) or {}
        self._svc.save_full_settings(payload)
        return jsonify({"success": True}), 200

    def get_accounts(self):
        data = self._svc.get_full_settings()
        return jsonify(data.get("accounts", [])), 200

    def save_account(self):
        payload = request.get_json(silent=True) or {}
        name = payload.get("name")
        if not name:
            abort(400, "Account name is required")
        self._svc._accounts.upsert(
            name=name,
            risk_usd=payload.get("risk_usd") or None,
            risk_pct=payload.get("risk_pct") or None,
            rr_ratio=payload.get("rr_ratio") or None,
        )
        return jsonify({"name": name}), 200

    def delete_account(self, name: str):
        self._svc._accounts.delete(name)
        return "", 204
