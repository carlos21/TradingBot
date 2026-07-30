"""NinjaTrader-specific streaming lifecycle service."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.services.nt_manager_service import NtManagerService
from src.services.settings_service import SettingsService
from src.utils.app_logger import ILogger

if TYPE_CHECKING:
    from src.infrastructure.gateway.datasource import ZMQDataSource


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

    def validate_before_start(self, data_source: ZMQDataSource | None) -> tuple[bool, str | None]:
        """Ensure at least one NinjaTrader account and an instrument are configured."""
        try:
            if not self._settings_service.list_accounts():
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

        return True, None

    def maybe_launch_after_delay(self, data_source: ZMQDataSource | None) -> None:
        """Auto-launch NinjaTrader only when it is neither connected nor running.

        Phase 1: poll the gateway once per second for up to 8 seconds — a
        healthy already-running NinjaTrader may connect on its own (the
        connector heartbeat interval is 5s, so a single check after 4s can
        miss it).
        Phase 2: still not connected — check whether a NinjaTrader process is
        running.  Only launch auto-login when it is not; launching the
        auto-login script against a running instance is disruptive (the
        script itself warns it works best from a closed state).
        """
        # Phase 1: give an already-running NinjaTrader a chance to connect.
        for _ in range(8):
            time.sleep(1)
            gateway = data_source.gateway if data_source else None
            if gateway and gateway.is_connected:
                self._logger.info("[NT Lifecycle] NinjaTrader connected on its own, skipping auto-login.")
                return

        # Phase 2: not connected — never auto-launch over a running instance.
        try:
            if self._nt_service.is_nt_running():
                # Keep waiting (up to 60s total from entry) in case the
                # connector is just slow to connect.
                for _ in range(52):
                    time.sleep(1)
                    gateway = data_source.gateway if data_source else None
                    if gateway and gateway.is_connected:
                        self._logger.info("[NT Lifecycle] NinjaTrader connected on its own, skipping auto-login.")
                        return
                self._logger.warning(
                    "[NT Lifecycle] NinjaTrader is already running but the connector has not connected. "
                    "Click Connect in the connector window — not auto-launching over a running instance."
                )
                return
        except Exception as e:
            self._logger.error(f"[NT Lifecycle] Failed to check running NinjaTrader processes: {e}")

        try:
            settings = self._settings_service.get_full_settings()
            creds = settings.get("credentials", {})
            username = creds.get("username", "")
            password = creds.get("password", "")

            if username and password:
                self._logger.info(
                    f"[NT Lifecycle] NinjaTrader not detected after 8s, launching auto-login for {username}..."
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
            return bool(self._settings_service.list_accounts())
        except Exception as e:
            self._logger.error(f"[NT Lifecycle] Failed to check accounts: {e}")
        return False
