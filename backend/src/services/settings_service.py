"""Service for loading and persisting application settings."""
from __future__ import annotations

import base64
from dataclasses import asdict
from typing import Protocol

from cryptography.fernet import Fernet

from src.config.models import DEFAULT_HISTORY_HOURS, AccountConfig
from src.domain.repositories import IInstrumentRegistry
from src.services.instrument_registry import InstrumentRegistry


class ISettingsRepository(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, is_sensitive: bool = False) -> None: ...
    def get_all(self) -> dict[str, str]: ...
    def delete(self, key: str) -> None: ...


class INtAccountRepository(Protocol):
    def list_accounts(self) -> list[AccountConfig]: ...
    def upsert(self, name: str, risk_usd: float | None = None, risk_pct: float | None = None,
               rr_ratio: float | None = None, live_enabled: bool = True,
               instrument_symbols: list[str] | None = None) -> None: ...
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
        instrument_registry: IInstrumentRegistry | None = None,
    ):
        self._settings = settings_repo
        self._accounts = accounts_repo
        self._creds = creds_repo
        self._fernet = self._make_fernet(secret_key)
        self._secret_key = secret_key
        self._instruments = instrument_registry or self._default_registry(settings_repo)

    @staticmethod
    def _default_registry(settings_repo: ISettingsRepository) -> IInstrumentRegistry:
        # Composition convenience for callers that do not inject a registry
        # (tests, ad-hoc constructions). The app composition root
        # (builder/app_factory) always injects the shared instance instead.
        from src.strategies.liquidity_v2.instrument_params import HardcodedInstrumentCatalog

        return InstrumentRegistry(settings_repo, HardcodedInstrumentCatalog())

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
        if not self._fernet:
            return ciphertext
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except Exception:
            # The credential may have been stored as plaintext before encryption
            # was enabled, or the secret key may have changed.  Returning the
            # raw value lets the UI display something useful while logging the
            # event via the caller.
            return ciphertext

    def get_full_settings(self) -> dict:
        """Return grouped settings for the UI."""
        all_settings = self._settings.get_all()
        accounts = self._accounts.list_accounts()
        cred = self._creds.get_credential(self._SERVICE_KEY)
        all_creds = self._creds.list_all()
        instruments = self._instruments.get_all()
        first = instruments[0]

        return {
            "trading": {
                "instruments": [asdict(inst) for inst in instruments],
                "pair": first.symbol,
                "instrument": first.full_name,
                "session_end": all_settings.get("session_end", "16:58"),
                "history_hours": all_settings.get(
                    "history_hours",
                    str(int(all_settings.get("history_days", str(DEFAULT_HISTORY_HOURS // 24))) * 24)
                    if "history_days" in all_settings
                    else str(DEFAULT_HISTORY_HOURS)
                ),
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
                {
                    "name": a.name,
                    "risk_usd": a.risk_usd,
                    "risk_pct": a.risk_pct,
                    "rr_ratio": a.rr_ratio,
                    "live_enabled": a.live_enabled if a.live_enabled is not None else True,
                    "instrument_symbols": list(a.instrument_symbols) if a.instrument_symbols else [],
                }
                for a in accounts
            ],
            "credentials": {
                "username": cred[0] if cred else "",
                "password": self._decrypt(cred[1]) if cred else "",
                "stored_usernames": [c["username"] for c in all_creds if c["service"] == self._SERVICE_KEY],
            },
            "mt_terminal_path": all_settings.get("mt_terminal_path", ""),
        }

    def save_full_settings(self, payload: dict) -> None:
        """Persist a grouped settings payload from the UI."""
        trading = payload.get("trading", {})
        network = payload.get("network", {})

        self._save_instruments(trading)
        self._sync_legacy_instrument_settings()

        for key, value in trading.items():
            if key in ("instruments", "pair", "instrument"):
                continue
            self._settings.set(key, str(value) if value is not None else "")
        for key, value in network.items():
            self._settings.set(key, str(value) if value is not None else "")

        # Only replace accounts when the payload explicitly contains them.
        # This prevents a credentials-only save (or any partial payload) from
        # accidentally wiping all configured NT accounts.
        if "accounts" in payload:
            accounts = payload.get("accounts", [])
            self._accounts.clear_all()
            for acct in accounts:
                self._accounts.upsert(
                    name=acct["name"],
                    risk_usd=acct.get("risk_usd") or None,
                    risk_pct=acct.get("risk_pct") or None,
                    rr_ratio=acct.get("rr_ratio") or None,
                    live_enabled=bool(acct.get("live_enabled", True)),
                    instrument_symbols=acct.get("instrument_symbols") or [],
                )

        credentials = payload.get("credentials", {})
        self._save_credentials_dict(credentials)

        mt_terminal_path = payload.get("mt_terminal_path", "")
        if mt_terminal_path:
            self._settings.set("mt_terminal_path", mt_terminal_path)

    def save_credentials(self, username: str, password: str) -> None:
        """Persist NinjaTrader credentials only, without touching accounts."""
        self._save_credentials_dict({"username": username, "password": password})

    def _save_credentials_dict(self, credentials: dict) -> None:
        """Internal helper: encrypt and store NT credentials if a username is given."""
        username = credentials.get("username", "")
        password = credentials.get("password", "")
        if username:
            self._creds.save_credential(
                self._SERVICE_KEY,
                username,
                self._encrypt(password),
            )

    def _save_instruments(self, trading: dict) -> None:
        """Persist full-name changes for catalog instruments.

        Instruments are hardcoded in the catalog — only ``full_name`` is
        editable (contract rollovers). Unknown symbols, symbol changes and
        point_value mutations are ignored.
        """
        instruments_payload = trading.get("instruments")
        current = {inst.symbol: inst for inst in self._instruments.get_all()}
        if instruments_payload is not None:
            for item in instruments_payload:
                if not isinstance(item, dict):
                    continue
                inst = current.get(item.get("symbol"))
                full_name = item.get("full_name")
                if inst is not None and full_name:
                    inst.full_name = full_name
            self._instruments.save(list(current.values()))
            return

        # Legacy fields: ``instrument`` updates the default instrument's full name.
        first = next(iter(current.values()))
        if "instrument" in trading and trading["instrument"]:
            first.full_name = trading["instrument"]
            self._instruments.save(list(current.values()))

    def _sync_legacy_instrument_settings(self) -> None:
        """Keep legacy ``pair`` / ``instrument`` settings in sync."""
        instruments = self._instruments.get_all()
        if not instruments:
            return
        first = instruments[0]
        self._settings.set("pair", first.symbol)
        self._settings.set("instrument", first.full_name)

    def save_account(self, payload: dict) -> None:
        """Persist a single account after validating numeric fields."""
        name = payload.get("name")
        if not name:
            raise ValueError("Account name is required")

        def _as_positive_float(value, field: str) -> float | None:
            if value is None or value == "":
                return None
            try:
                num = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} must be a number") from exc
            if num < 0:
                raise ValueError(f"{field} must be non-negative")
            return num

        live_enabled = payload.get("live_enabled")
        if live_enabled is None:
            live_enabled = True

        self._accounts.upsert(
            name=name,
            risk_usd=_as_positive_float(payload.get("risk_usd"), "risk_usd"),
            risk_pct=_as_positive_float(payload.get("risk_pct"), "risk_pct"),
            rr_ratio=_as_positive_float(payload.get("rr_ratio"), "rr_ratio"),
            live_enabled=bool(live_enabled),
            instrument_symbols=payload.get("instrument_symbols") or [],
        )

    def delete_account(self, name: str) -> None:
        """Delete an account by name."""
        self._accounts.delete(name)

    def list_accounts(self) -> list[AccountConfig]:
        """Return all configured NinjaTrader accounts."""
        return self._accounts.list_accounts()

    def get_instruments(self) -> list[dict]:
        """Return the current instrument registry as plain dictionaries."""
        return [asdict(inst) for inst in self._instruments.get_all()]

    def to_app_config_overrides(self) -> dict:
        """Return a flat dict suitable for DbConfigLoader."""
        all_settings = self._settings.get_all()
        accounts = self._accounts.list_accounts()

        overrides = {}
        for key, value in all_settings.items():
            if key == "instruments":
                continue
            overrides[key] = value

        instruments = self._instruments.get_all()
        if instruments:
            first = instruments[0]
            overrides["pair"] = first.symbol
            overrides["instrument"] = first.full_name
            overrides["point_value"] = str(first.point_value)

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
