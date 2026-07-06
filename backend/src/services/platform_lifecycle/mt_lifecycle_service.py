"""MetaTrader-specific streaming lifecycle service."""
from __future__ import annotations

import time

from src.services.mt_manager_service import MetaTraderManagerService
from src.services.settings_service import SettingsService
from src.utils.app_logger import ILogger


class MetaTraderLifecycleService:
    """Handles MetaTrader terminal launch for streaming."""

    def __init__(
        self,
        mt_service: MetaTraderManagerService,
        logger: ILogger,
        settings_service: SettingsService | None = None,
    ):
        self._mt_service = mt_service
        self._logger = logger
        self._settings_service = settings_service

    def _get_terminal_path(self) -> str | None:
        """Read optional MT terminal path from settings."""
        if self._settings_service is None:
            return None
        try:
            settings = self._settings_service.get_full_settings()
            path = settings.get("mt_terminal_path", "")
            return path if path else None
        except Exception:
            return None

    def validate_before_start(self, data_source) -> tuple[bool, str | None]:
        """MetaTrader manages its own accounts; no pre-flight check needed."""
        return True, None

    def maybe_launch_after_delay(self, data_source) -> None:
        """Wait a few seconds, then launch MetaTrader terminal if not connected."""
        time.sleep(4)

        gateway = getattr(data_source, "gateway", None)
        if gateway and getattr(gateway, "is_connected", False):
            self._logger.info("[MT Lifecycle] MetaTrader connected on its own, skipping launch.")
            return

        self._logger.info("[MT Lifecycle] MetaTrader not detected after 4s, checking terminal...")

        if self._mt_service.is_terminal_running():
            self._logger.info(
                "[MT Lifecycle] MetaTrader terminal is running but not connected yet. "
                "Ensure the TradingBotZmqEA is attached to a chart with autoConnectOnStartup enabled."
            )
            return

        override_path = self._get_terminal_path()
        if override_path:
            self._logger.info(f"[MT Lifecycle] Using configured terminal path: {override_path}")
        else:
            self._logger.info("[MT Lifecycle] No terminal path configured; attempting auto-detection.")

        result = self._mt_service.launch_terminal(exe_path=override_path)
        if not result.get("success"):
            self._logger.warning(f"[MT Lifecycle] MT launch warning: {result.get('message')}")
        else:
            self._logger.info(f"[MT Lifecycle] {result.get('message')}")

    def has_accounts_configured(self) -> bool:
        """MetaTrader manages accounts internally; always return True."""
        return True
