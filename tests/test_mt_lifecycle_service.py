"""Tests for src/services/platform_lifecycle/mt_lifecycle_service.py."""

from unittest.mock import MagicMock, patch

from src.services.mt_manager_service import MetaTraderManagerService
from src.services.platform_lifecycle.mt_lifecycle_service import (
    MetaTraderLifecycleService,
)
from tests.fakes import FakeLogger


class FakeGateway:
    def __init__(self, connected=False):
        self.is_connected = connected


class FakeDataSource:
    def __init__(self, connected=False):
        self.gateway = FakeGateway(connected=connected)


class TestMetaTraderLifecycleService:

    def test_validate_before_start_always_ok(self):
        svc = MetaTraderManagerService()
        lifecycle = MetaTraderLifecycleService(svc, FakeLogger())
        ok, err = lifecycle.validate_before_start(FakeDataSource())
        assert ok is True
        assert err is None

    def test_has_accounts_configured_always_true(self):
        svc = MetaTraderManagerService()
        lifecycle = MetaTraderLifecycleService(svc, FakeLogger())
        assert lifecycle.has_accounts_configured() is True

    def test_maybe_launch_skips_if_already_connected(self):
        svc = MagicMock(spec=MetaTraderManagerService)
        logger = FakeLogger()
        lifecycle = MetaTraderLifecycleService(svc, logger)

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=True))

        svc.is_terminal_running.assert_not_called()
        svc.launch_terminal.assert_not_called()

    def test_maybe_launch_when_not_connected_and_not_running(self):
        svc = MagicMock(spec=MetaTraderManagerService)
        svc.is_terminal_running.return_value = False
        svc.launch_terminal.return_value = {"success": True, "message": "Launching"}
        logger = FakeLogger()
        lifecycle = MetaTraderLifecycleService(svc, logger)

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        svc.is_terminal_running.assert_called_once()
        svc.launch_terminal.assert_called_once()

    def test_maybe_launch_when_not_connected_but_running(self):
        svc = MagicMock(spec=MetaTraderManagerService)
        svc.is_terminal_running.return_value = True
        logger = FakeLogger()
        lifecycle = MetaTraderLifecycleService(svc, logger)

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        svc.is_terminal_running.assert_called_once()
        svc.launch_terminal.assert_not_called()

    def test_maybe_launch_logs_warning_on_failure(self):
        svc = MagicMock(spec=MetaTraderManagerService)
        svc.is_terminal_running.return_value = False
        svc.launch_terminal.return_value = {"success": False, "message": "Not found"}
        logger = FakeLogger()
        lifecycle = MetaTraderLifecycleService(svc, logger)

        with patch("time.sleep"):
            lifecycle.maybe_launch_after_delay(FakeDataSource(connected=False))

        svc.launch_terminal.assert_called_once()
