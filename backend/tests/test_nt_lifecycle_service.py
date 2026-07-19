"""Tests for src/services/platform_lifecycle/nt_lifecycle_service.py."""

from unittest.mock import MagicMock, patch

from src.services.nt_manager_service import NtManagerService
from src.services.platform_lifecycle.nt_lifecycle_service import (
    NinjaTraderLifecycleService,
)
from tests.fakes import FakeLogger


class FakeGateway:
    def __init__(self, connected=False):
        self.is_connected = connected


class FakeDataSource:
    def __init__(self, connected=False):
        self.gateway = FakeGateway(connected=connected)


class FakeSettingsService:
    def __init__(self, accounts=None, credentials=None, instrument="MNQ 09-26"):
        self._accounts = accounts or []
        self._credentials = credentials or {}
        self._instrument = instrument

    def list_accounts(self):
        return self._accounts

    def get_full_settings(self):
        return {
            "credentials": self._credentials,
            "trading": {"instrument": self._instrument},
        }


class TestNinjaTraderLifecycleService:

    def test_validate_before_start_with_accounts(self):
        nt_svc = MagicMock(spec=NtManagerService)
        settings = FakeSettingsService(accounts=[{"name": "Sim101"}])
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())
        ok, err = lifecycle.validate_before_start(FakeDataSource())
        assert ok is True
        assert err is None

    def test_validate_before_start_without_accounts(self):
        nt_svc = MagicMock(spec=NtManagerService)
        settings = FakeSettingsService(accounts=[])
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())
        ok, err = lifecycle.validate_before_start(FakeDataSource())
        assert ok is False
        assert "No NinjaTrader accounts configured" in err

    def test_validate_before_start_without_instrument(self):
        nt_svc = MagicMock(spec=NtManagerService)
        settings = FakeSettingsService(accounts=[{"name": "Sim101"}], instrument="")
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())
        ok, err = lifecycle.validate_before_start(FakeDataSource())
        assert ok is False
        assert "No trading instrument configured" in err

    def test_validate_before_start_no_repo(self):
        nt_svc = MagicMock(spec=NtManagerService)
        settings = MagicMock()
        del settings._accounts
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())
        ok, err = lifecycle.validate_before_start(FakeDataSource())
        assert ok is True
        assert err is None

    def test_has_accounts_configured_true(self):
        nt_svc = MagicMock(spec=NtManagerService)
        settings = FakeSettingsService(accounts=[{"name": "Sim101"}])
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())
        assert lifecycle.has_accounts_configured() is True

    def test_has_accounts_configured_false(self):
        nt_svc = MagicMock(spec=NtManagerService)
        settings = FakeSettingsService(accounts=[])
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())
        assert lifecycle.has_accounts_configured() is False

    def test_maybe_launch_skips_if_connected(self):
        nt_svc = MagicMock(spec=NtManagerService)
        settings = FakeSettingsService()
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=True))

        nt_svc.open_nt_and_login.assert_not_called()

    def test_maybe_launch_with_credentials(self):
        nt_svc = MagicMock(spec=NtManagerService)
        nt_svc.open_nt_and_login.return_value = {"success": True, "message": "ok"}
        settings = FakeSettingsService(credentials={"username": "user", "password": "pass"})
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        nt_svc.open_nt_and_login.assert_called_once_with("user", "pass")

    def test_maybe_launch_without_credentials(self):
        nt_svc = MagicMock(spec=NtManagerService)
        settings = FakeSettingsService(credentials={})
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        nt_svc.open_nt_and_login.assert_not_called()

    def test_maybe_launch_logs_warning_on_failure(self):
        nt_svc = MagicMock(spec=NtManagerService)
        nt_svc.open_nt_and_login.return_value = {"success": False, "message": "failed"}
        settings = FakeSettingsService(credentials={"username": "user", "password": "pass"})
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        nt_svc.open_nt_and_login.assert_called_once()
