"""Tests for src/services/settings_service.py."""

import pytest

from src.config.models import AccountConfig
from src.services.settings_service import SettingsService
from tests.fakes import (
    FakeCredentialRepository,
    FakeNtAccountRepository,
    FakeSettingsRepository,
)


def _make_service(secret_key=None):
    return SettingsService(
        settings_repo=FakeSettingsRepository(),
        accounts_repo=FakeNtAccountRepository(),
        creds_repo=FakeCredentialRepository(),
        secret_key=secret_key,
    )


class TestSettingsServiceInitialization:

    def test_init_without_secret_key(self):
        svc = _make_service(secret_key=None)
        assert svc._fernet is None

    def test_init_with_secret_key(self):
        svc = _make_service(secret_key="my-secret-key-1234567890")
        assert svc._fernet is not None
        assert svc._secret_key == "my-secret-key-1234567890"


class TestSettingsServiceGetFullSettings:

    def test_defaults(self):
        svc = _make_service()
        settings = svc.get_full_settings()
        assert settings["trading"]["pair"] == "MNQ"
        assert settings["trading"]["instrument"] == "MNQ 06-26"
        assert settings["network"]["flask_port"] == "5001"
        assert settings["network"]["zmq_host"] == "127.0.0.1"
        assert settings["accounts"] == []
        assert settings["credentials"]["username"] == ""
        assert settings["credentials"]["password"] == ""

    def test_with_accounts(self):
        svc = _make_service()
        svc._accounts.upsert("TestAccount", risk_usd=100.0, risk_pct=1.0, rr_ratio=3.0)
        settings = svc.get_full_settings()
        assert len(settings["accounts"]) == 1
        assert settings["accounts"][0]["name"] == "TestAccount"
        assert settings["accounts"][0]["risk_usd"] == 100.0
        assert settings["accounts"][0]["risk_pct"] == 1.0
        assert settings["accounts"][0]["rr_ratio"] == 3.0

    def test_with_credentials(self):
        svc = _make_service()
        svc._creds.save_credential("ninjatrader", "user1", "pass1")
        settings = svc.get_full_settings()
        assert settings["credentials"]["username"] == "user1"
        assert settings["credentials"]["password"] == "pass1"

    def test_with_encrypted_credentials(self):
        svc = _make_service(secret_key="my-secret-key-1234567890")
        svc._creds.save_credential("ninjatrader", "user1", svc._encrypt("secret123"))
        settings = svc.get_full_settings()
        assert settings["credentials"]["password"] == "secret123"

    def test_with_custom_settings(self):
        svc = _make_service()
        svc._settings.set("pair", "ES")
        svc._settings.set("flask_port", "8080")
        settings = svc.get_full_settings()
        assert settings["trading"]["pair"] == "ES"
        assert settings["network"]["flask_port"] == "8080"


class TestSettingsServiceSaveFullSettings:

    def test_save_trading_settings(self):
        svc = _make_service()
        svc.save_full_settings({
            "trading": {"pair": "ES", "instrument": "ES 06-26"},
            "network": {},
            "accounts": [],
            "credentials": {},
        })
        assert svc._settings.get("pair") == "ES"
        assert svc._settings.get("instrument") == "ES 06-26"

    def test_save_network_settings(self):
        svc = _make_service()
        svc.save_full_settings({
            "trading": {},
            "network": {"flask_port": "8080", "zmq_host": "0.0.0.0"},
            "accounts": [],
            "credentials": {},
        })
        assert svc._settings.get("flask_port") == "8080"
        assert svc._settings.get("zmq_host") == "0.0.0.0"

    def test_save_accounts(self):
        svc = _make_service()
        svc.save_full_settings({
            "trading": {},
            "network": {},
            "accounts": [
                {"name": "Acct1", "risk_usd": 100.0, "risk_pct": 1.0, "rr_ratio": 3.0},
                {"name": "Acct2", "risk_usd": 200.0},
            ],
            "credentials": {},
        })
        accounts = svc._accounts.list_accounts()
        assert len(accounts) == 2
        assert accounts[0].name == "Acct1"
        assert accounts[0].risk_usd == 100.0
        assert accounts[1].name == "Acct2"
        assert accounts[1].risk_usd == 200.0

    def test_save_accounts_clears_existing(self):
        svc = _make_service()
        svc._accounts.upsert("OldAccount", risk_usd=50.0)
        svc.save_full_settings({
            "trading": {},
            "network": {},
            "accounts": [{"name": "NewAccount", "risk_usd": 100.0}],
            "credentials": {},
        })
        accounts = svc._accounts.list_accounts()
        assert len(accounts) == 1
        assert accounts[0].name == "NewAccount"

    def test_save_credentials(self):
        svc = _make_service()
        svc.save_full_settings({
            "trading": {},
            "network": {},
            "accounts": [],
            "credentials": {"username": "user1", "password": "pass1"},
        })
        cred = svc._creds.get_credential("ninjatrader")
        assert cred == ("user1", "pass1")

    def test_save_credentials_with_encryption(self):
        svc = _make_service(secret_key="my-secret-key-1234567890")
        svc.save_full_settings({
            "trading": {},
            "network": {},
            "accounts": [],
            "credentials": {"username": "user1", "password": "secret123"},
        })
        cred = svc._creds.get_credential("ninjatrader")
        assert cred[0] == "user1"
        # Password should be encrypted
        assert cred[1] != "secret123"
        # Verify it decrypts correctly
        assert svc._decrypt(cred[1]) == "secret123"

    def test_save_credentials_missing_username(self):
        svc = _make_service()
        svc.save_full_settings({
            "trading": {},
            "network": {},
            "accounts": [],
            "credentials": {"username": "", "password": "pass1"},
        })
        assert svc._creds.get_credential("ninjatrader") is None

    def test_save_none_values(self):
        svc = _make_service()
        svc.save_full_settings({
            "trading": {"pair": None},
            "network": {},
            "accounts": [],
            "credentials": {},
        })
        assert svc._settings.get("pair") == ""


class TestSettingsServiceToAppConfigOverrides:

    def test_empty(self):
        svc = _make_service()
        overrides = svc.to_app_config_overrides()
        assert overrides == {}

    def test_with_settings(self):
        svc = _make_service()
        svc._settings.set("pair", "ES")
        svc._settings.set("flask_port", "8080")
        overrides = svc.to_app_config_overrides()
        assert overrides["pair"] == "ES"
        assert overrides["flask_port"] == "8080"

    def test_with_accounts(self):
        svc = _make_service()
        svc._accounts.upsert("Acct1", risk_usd=100.0, risk_pct=1.0, rr_ratio=3.0)
        overrides = svc.to_app_config_overrides()
        assert "nt_accounts" in overrides
        assert overrides["risk_per_trade"] == 100.0
        assert overrides["risk_pct_per_trade"] == 1.0
        assert overrides["rr_ratio"] == 3.0


class TestSettingsServiceEncryption:

    def test_encrypt_decrypt_no_fernet(self):
        svc = _make_service(secret_key=None)
        assert svc._encrypt("hello") == "hello"
        assert svc._decrypt("hello") == "hello"

    def test_encrypt_decrypt_with_fernet(self):
        svc = _make_service(secret_key="my-secret-key-1234567890")
        encrypted = svc._encrypt("hello")
        assert encrypted != "hello"
        assert svc._decrypt(encrypted) == "hello"
