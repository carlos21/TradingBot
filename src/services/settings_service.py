"""Service for loading and persisting application settings."""
from __future__ import annotations

import base64
import os
from typing import Protocol

from cryptography.fernet import Fernet

from src.config.models import AccountConfig, AppConfig
from src.infrastructure.repositories.accounts_repository import NtAccountRepository
from src.infrastructure.repositories.credentials_repository import CredentialRepository
from src.infrastructure.repositories.settings_repository import SettingsRepository


class ISettingsRepository(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, is_sensitive: bool = False) -> None: ...
    def get_all(self) -> dict[str, str]: ...
    def delete(self, key: str) -> None: ...


class INtAccountRepository(Protocol):
    def list_accounts(self) -> list[AccountConfig]: ...
    def upsert(self, name: str, risk_usd: float | None = None, risk_pct: float | None = None) -> None: ...
    def delete(self, name: str) -> None: ...
    def clear_all(self) -> None: ...


class ICredentialRepository(Protocol):
    def get_credential(self, service: str) -> tuple[str, str] | None: ...
    def save_credential(self, service: str, username: str, password_encrypted: str) -> None: ...


class SettingsService:
    """Orchestrates settings persistence with optional credential encryption."""

    _SERVICE_KEY = "ninjatrader"

    def __init__(
        self,
        settings_repo: ISettingsRepository,
        accounts_repo: INtAccountRepository,
        creds_repo: ICredentialRepository,
        secret_key: str | None = None,
    ):
        self._settings = settings_repo
        self._accounts = accounts_repo
        self._creds = creds_repo
        self._fernet = self._make_fernet(secret_key)
        self._secret_key = secret_key

    @staticmethod
    def _make_fernet(secret_key: str | None) -> Fernet | None:
        if not secret_key:
            return None
        # Fernet requires a 32-byte base64-encoded key
        key = base64.urlsafe_b64encode(secret_key.ljust(32)[:32].encode())
        return Fernet(key)

    def _encrypt(self, plaintext: str) -> str:
        if self._fernet:
            return self._fernet.encrypt(plaintext.encode()).decode()
        return plaintext

    def _decrypt(self, ciphertext: str) -> str:
        if self._fernet:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        return ciphertext

    def get_full_settings(self) -> dict:
        """Return grouped settings for the UI."""
        all_settings = self._settings.get_all()
        accounts = self._accounts.list_accounts()
        cred = self._creds.get_credential(self._SERVICE_KEY)
        all_creds = self._creds.list_all()

        return {
            "trading": {
                "pair": all_settings.get("pair", "MNQ"),
                "instrument": all_settings.get("instrument", "MNQ 06-26"),
            },
            "network": {
                "flask_port": all_settings.get("flask_port", "5001"),
                "zmq_host": all_settings.get("zmq_host", "127.0.0.1"),
                "zmq_market_port": all_settings.get("zmq_market_port", "5555"),
                "zmq_command_port": all_settings.get("zmq_command_port", "5556"),
                "zmq_query_port": all_settings.get("zmq_query_port", "5557"),
                "zmq_heartbeat_port": all_settings.get("zmq_heartbeat_port", "5558"),
            },
            "accounts": [
                {"name": a.name, "risk_usd": a.risk_usd, "risk_pct": a.risk_pct, "rr_ratio": a.rr_ratio}
                for a in accounts
            ],
            "credentials": {
                "username": cred[0] if cred else "",
                "password": self._decrypt(cred[1]) if cred else "",
                "stored_usernames": [c["username"] for c in all_creds if c["service"] == self._SERVICE_KEY],
            },
        }

    def save_full_settings(self, payload: dict) -> None:
        """Persist a grouped settings payload from the UI."""
        trading = payload.get("trading", {})
        network = payload.get("network", {})
        accounts = payload.get("accounts", [])
        credentials = payload.get("credentials", {})

        for key, value in trading.items():
            self._settings.set(key, str(value) if value is not None else "")
        for key, value in network.items():
            self._settings.set(key, str(value) if value is not None else "")

        self._accounts.clear_all()
        for acct in accounts:
            self._accounts.upsert(
                name=acct["name"],
                risk_usd=acct.get("risk_usd") or None,
                risk_pct=acct.get("risk_pct") or None,
                rr_ratio=acct.get("rr_ratio") or None,
            )

        username = credentials.get("username", "")
        password = credentials.get("password", "")
        if username:
            self._creds.save_credential(
                self._SERVICE_KEY,
                username,
                self._encrypt(password),
            )

    def to_app_config_overrides(self) -> dict:
        """Return a flat dict suitable for DbConfigLoader."""
        all_settings = self._settings.get_all()
        accounts = self._accounts.list_accounts()

        overrides = {}
        for key, value in all_settings.items():
            overrides[key] = value

        if accounts:
            overrides["nt_accounts"] = accounts
            # Derive global defaults from first account for backtest/strategy compatibility
            first = accounts[0]
            if first.risk_usd is not None:
                overrides["risk_per_trade"] = first.risk_usd
            if first.risk_pct is not None:
                overrides["risk_pct_per_trade"] = first.risk_pct
            if first.rr_ratio is not None:
                overrides["rr_ratio"] = first.rr_ratio
        return overrides
