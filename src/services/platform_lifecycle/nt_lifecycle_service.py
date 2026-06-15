"""NinjaTrader-specific streaming lifecycle service."""
from __future__ import annotations

import time

from src.services.nt_manager_service import NtManagerService
from src.services.settings_service import SettingsService
from src.utils.app_logger import ILogger


class NinjaTraderLifecycleService:
    """Handles NinjaTrader account validation and auto-login for streaming."""

    def __init__(
        self,
        nt_service: NtManagerService,
        settings_service: SettingsService,
        logger: ILogger,
    ):
        self._nt_service = nt_service
        self._settings_service = settings_service
        self._logger = logger

    def validate_before_start(self, data_source) -> tuple[bool, str | None]:
        """Ensure at least one NinjaTrader account and an instrument are configured."""
        try:
            accounts_repo = getattr(self._settings_service, "_accounts", None)
            if accounts_repo is not None:
                account_list = accounts_repo.list_accounts()
                if not account_list:
                    return (
                        False,
                        "No NinjaTrader accounts configured. Go to Admin → Settings and add at least one account before starting streaming.",
                    )
        except Exception as e:
            self._logger.error(f"[NT Lifecycle] Failed to check accounts: {e}")

        try:
            settings = self._settings_service.get_full_settings()
            instrument = settings.get("trading", {}).get("instrument", "")
            if not instrument:
                return (
                    False,
                    "No trading instrument configured. Go to Admin → Settings and set the Instrument (e.g. 'MNQ 09-26') before starting streaming.",
                )
        except Exception as e:
            self._logger.error(f"[NT Lifecycle] Failed to check instrument: {e}")

        return True, None

    def maybe_launch_after_delay(self, data_source) -> None:
        """Wait a few seconds, then auto-launch NinjaTrader if not connected."""
        time.sleep(4)

        gateway = getattr(data_source, "gateway", None)
        if gateway and getattr(gateway, "is_connected", False):
            self._logger.info("[NT Lifecycle] NinjaTrader connected on its own, skipping auto-login.")
            return

        try:
            settings = self._settings_service.get_full_settings()
            creds = settings.get("credentials", {})
            username = creds.get("username", "")
            password = creds.get("password", "")

            if username and password:
                self._logger.info(
                    f"[NT Lifecycle] NinjaTrader not detected after 4s, launching auto-login for {username}..."
                )
                result = self._nt_service.open_nt_and_login(username, password)
                if not result.get("success"):
                    self._logger.warning(f"[NT Lifecycle] NT launch warning: {result.get('message')}")
            else:
                self._logger.warning(
                    "[NT Lifecycle] No NT credentials configured. Please connect NinjaTrader manually."
                )
        except Exception as e:
            self._logger.error(f"[NT Lifecycle] Failed to launch NinjaTrader: {e}")

    def has_accounts_configured(self) -> bool:
        """Return True if at least one NT account exists in the DB."""
        try:
            accounts_repo = getattr(self._settings_service, "_accounts", None)
            if accounts_repo is not None:
                return bool(accounts_repo.list_accounts())
        except Exception as e:
            self._logger.error(f"[NT Lifecycle] Failed to check accounts: {e}")
        return False
