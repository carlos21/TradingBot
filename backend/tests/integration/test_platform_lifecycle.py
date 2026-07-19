"""Integration tests for platform lifecycle services.

These tests wire the lifecycle services with fake manager services and a
mocked settings service to validate pre-flight checks and auto-launch logic
without touching real platforms.
"""

from unittest.mock import MagicMock

import pytest

from src.services.platform_lifecycle.mt_lifecycle_service import MetaTraderLifecycleService
from src.services.platform_lifecycle.nt_lifecycle_service import NinjaTraderLifecycleService
from src.utils.app_logger import ILogger


class NoOpLogger(ILogger):
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def logger():
    return NoOpLogger()


class TestNinjaTraderLifecycleService:
    def _service(self, accounts=None, settings=None, nt_service=None):
        settings_service = MagicMock()
        settings_service.get_full_settings.return_value = settings or {}
        settings_service.list_accounts.return_value = accounts or []
        nt_service = nt_service or MagicMock()
        return NinjaTraderLifecycleService(nt_service, settings_service, NoOpLogger())

    def test_validate_before_start_no_accounts(self):
        svc = self._service()
        ok, msg = svc.validate_before_start(None)
        assert ok is False
        assert "accounts" in msg.lower()

    def test_validate_before_start_no_instrument(self):
        svc = self._service(accounts=[{"name": "Sim101"}])
        ok, msg = svc.validate_before_start(None)
        assert ok is False
        assert "instrument" in msg.lower()

    def test_validate_before_start_ready(self):
        svc = self._service(
            accounts=[{"name": "Sim101"}],
            settings={"trading": {"instrument": "MNQ 09-26"}},
        )
        ok, msg = svc.validate_before_start(None)
        assert ok is True
        assert msg is None

    def test_validate_before_start_settings_exception_is_logged(self, logger):
        settings_service = MagicMock()
        settings_service.get_full_settings.side_effect = RuntimeError("boom")
        svc = NinjaTraderLifecycleService(MagicMock(), settings_service, logger)
        ok, msg = svc.validate_before_start(None)
        assert ok is True
        assert msg is None

    def test_has_accounts_configured_true(self):
        svc = self._service(accounts=[{"name": "Sim101"}])
        assert svc.has_accounts_configured() is True

    def test_has_accounts_configured_false(self):
        svc = self._service()
        assert svc.has_accounts_configured() is False

    def test_maybe_launch_after_delay_already_connected(self, monkeypatch):
        svc = self._service(
            settings={"credentials": {"username": "u", "password": "p"}},
        )
        monkeypatch.setattr("time.sleep", lambda _s: None)
        data_source = MagicMock()
        data_source.gateway.is_connected = True
        svc.maybe_launch_after_delay(data_source)
        svc._nt_service.open_nt_and_login.assert_not_called()

    def test_maybe_launch_after_delay_auto_login(self, monkeypatch):
        nt_service = MagicMock()
        nt_service.open_nt_and_login.return_value = {"success": True}
        svc = self._service(
            nt_service=nt_service,
            settings={"credentials": {"username": "u", "password": "p"}},
        )
        monkeypatch.setattr("time.sleep", lambda _s: None)
        data_source = MagicMock()
        data_source.gateway.is_connected = False
        svc.maybe_launch_after_delay(data_source)
        nt_service.open_nt_and_login.assert_called_once_with("u", "p")

    def test_maybe_launch_after_delay_no_credentials(self, monkeypatch):
        nt_service = MagicMock()
        svc = self._service(nt_service=nt_service)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        data_source = MagicMock()
        data_source.gateway.is_connected = False
        svc.maybe_launch_after_delay(data_source)
        nt_service.open_nt_and_login.assert_not_called()


class TestMetaTraderLifecycleService:
    def _service(self, mt_service=None, settings_service=None):
        mt_service = mt_service or MagicMock()
        return MetaTraderLifecycleService(mt_service, NoOpLogger(), settings_service)

    def test_validate_before_start_always_ok(self):
        svc = self._service()
        ok, msg = svc.validate_before_start(None)
        assert ok is True
        assert msg is None

    def test_has_accounts_configured_true(self):
        svc = self._service()
        assert svc.has_accounts_configured() is True

    def test_maybe_launch_after_delay_already_connected(self, monkeypatch):
        mt_service = MagicMock()
        mt_service.is_terminal_running.return_value = False
        svc = self._service(mt_service=mt_service)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        data_source = MagicMock()
        data_source.gateway.is_connected = True
        svc.maybe_launch_after_delay(data_source)
        mt_service.launch_terminal.assert_not_called()

    def test_maybe_launch_after_delay_terminal_running(self, monkeypatch):
        mt_service = MagicMock()
        mt_service.is_terminal_running.return_value = True
        svc = self._service(mt_service=mt_service)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        data_source = MagicMock()
        data_source.gateway.is_connected = False
        svc.maybe_launch_after_delay(data_source)
        mt_service.launch_terminal.assert_not_called()

    def test_maybe_launch_after_delay_launch_with_override_path(self, monkeypatch):
        mt_service = MagicMock()
        mt_service.is_terminal_running.return_value = False
        mt_service.launch_terminal.return_value = {"success": True, "message": "ok"}
        settings_service = MagicMock()
        settings_service.get_full_settings.return_value = {"mt_terminal_path": "/some/terminal.exe"}
        svc = self._service(mt_service=mt_service, settings_service=settings_service)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        data_source = MagicMock()
        data_source.gateway.is_connected = False
        svc.maybe_launch_after_delay(data_source)
        mt_service.launch_terminal.assert_called_once_with(exe_path="/some/terminal.exe")

    def test_maybe_launch_after_delay_launch_failed(self, monkeypatch):
        mt_service = MagicMock()
        mt_service.is_terminal_running.return_value = False
        mt_service.launch_terminal.return_value = {"success": False, "message": "nope"}
        svc = self._service(mt_service=mt_service)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        data_source = MagicMock()
        data_source.gateway.is_connected = False
        svc.maybe_launch_after_delay(data_source)
        mt_service.launch_terminal.assert_called_once_with(exe_path=None)

    def test_get_terminal_path_no_settings(self):
        svc = self._service()
        assert svc._get_terminal_path() is None

    def test_get_terminal_path_empty_string(self):
        settings_service = MagicMock()
        settings_service.get_full_settings.return_value = {"mt_terminal_path": ""}
        svc = self._service(settings_service=settings_service)
        assert svc._get_terminal_path() is None
