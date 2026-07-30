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
    def __init__(self, accounts=None, credentials=None, instrument="MNQ 09-26", pair="MNQ"):
        self._accounts = accounts or []
        self._credentials = credentials or {}
        self._instrument = instrument
        self._pair = pair

    def list_accounts(self):
        return self._accounts

    def get_full_settings(self):
        return {
            "credentials": self._credentials,
            "trading": {"instrument": self._instrument, "pair": self._pair},
        }


class TestNinjaTraderLifecycleService:

    def test_validate_before_start_with_accounts(self):
        nt_svc = MagicMock(spec=NtManagerService)
        settings = FakeSettingsService(
            accounts=[{"name": "Sim101", "live_enabled": True, "instrument_symbols": ["MNQ"]}]
        )
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
        settings.get_full_settings.side_effect = RuntimeError("no repo")
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

    def test_maybe_launch_skips_if_connects_during_phase1_poll(self):
        """A slow-but-healthy NT connecting within the 8s poll is not launched over."""
        nt_svc = MagicMock(spec=NtManagerService)
        settings = FakeSettingsService(credentials={"username": "user", "password": "pass"})
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())

        data_source = FakeDataSource(connected=False)

        sleep_calls = {"n": 0}

        def _fake_sleep(_seconds):
            sleep_calls["n"] += 1
            if sleep_calls["n"] >= 3:
                data_source.gateway.is_connected = True

        with patch("time.sleep", side_effect=_fake_sleep):
            lifecycle.maybe_launch_after_delay(data_source)

        nt_svc.open_nt_and_login.assert_not_called()

    def test_maybe_launch_with_credentials(self):
        nt_svc = MagicMock(spec=NtManagerService)
        nt_svc.is_nt_running.return_value = False
        nt_svc.open_nt_and_login.return_value = {"success": True, "message": "ok"}
        settings = FakeSettingsService(credentials={"username": "user", "password": "pass"})
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        nt_svc.open_nt_and_login.assert_called_once_with("user", "pass")

    def test_maybe_launch_without_credentials(self):
        nt_svc = MagicMock(spec=NtManagerService)
        nt_svc.is_nt_running.return_value = False
        settings = FakeSettingsService(credentials={})
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        nt_svc.open_nt_and_login.assert_not_called()

    def test_maybe_launch_logs_warning_on_failure(self):
        nt_svc = MagicMock(spec=NtManagerService)
        nt_svc.is_nt_running.return_value = False
        nt_svc.open_nt_and_login.return_value = {"success": False, "message": "failed"}
        settings = FakeSettingsService(credentials={"username": "user", "password": "pass"})
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        nt_svc.open_nt_and_login.assert_called_once()

    def test_maybe_launch_never_launches_over_running_nt(self):
        """NT already running but not connected: warn, do NOT auto-launch."""
        nt_svc = MagicMock(spec=NtManagerService)
        nt_svc.is_nt_running.return_value = True
        settings = FakeSettingsService(credentials={"username": "user", "password": "pass"})
        logger = MagicMock()
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, logger)

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        nt_svc.open_nt_and_login.assert_not_called()
        assert any(
            "already running but the connector has not connected" in str(call)
            for call in logger.warning.call_args_list
        )

    def test_maybe_launch_running_nt_connects_during_phase2_poll(self):
        """NT running: keep polling; if it connects, return without launching."""
        nt_svc = MagicMock(spec=NtManagerService)
        nt_svc.is_nt_running.return_value = True
        settings = FakeSettingsService(credentials={"username": "user", "password": "pass"})
        lifecycle = NinjaTraderLifecycleService(nt_svc, settings, FakeLogger())

        data_source = FakeDataSource(connected=False)

        sleep_calls = {"n": 0}

        def _fake_sleep(_seconds):
            sleep_calls["n"] += 1
            # Phase 1 takes 8 polls; connect shortly into phase 2.
            if sleep_calls["n"] >= 12:
                data_source.gateway.is_connected = True

        with patch("time.sleep", side_effect=_fake_sleep):
            lifecycle.maybe_launch_after_delay(data_source)

        nt_svc.open_nt_and_login.assert_not_called()
