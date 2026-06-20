import html
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor

import requests


class Notifier(ABC):
    @abstractmethod
    def send(self, message: str) -> None: ...


class NoOpNotifier(Notifier):
    def send(self, message: str) -> None:
        pass


class TelegramNotifier(Notifier):
    """Async Telegram notifier with HTML escaping, length limits, and failure logging."""

    MAX_MESSAGE_LENGTH = 4096

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        max_workers: int = 4,
        logger: logging.Logger | None = None,
    ):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._logger = logger or logging.getLogger(__name__)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="telegram")
        self._shutdown = False

    def send(self, message: str) -> None:
        if self._shutdown:
            self._logger.warning("[TelegramNotifier] send() called after shutdown; ignoring")
            return
        self._executor.submit(self._send, message)

    def _send(self, message: str) -> None:
        text = html.escape(message)
        if len(text) > self.MAX_MESSAGE_LENGTH:
            text = text[: self.MAX_MESSAGE_LENGTH - 3] + "..."

        try:
            response = requests.post(
                self._url,
                json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if not response.ok:
                self._logger.error(
                    f"[TelegramNotifier] send failed: {response.status_code} {response.text[:200]}"
                )
        except Exception as exc:
            self._logger.error(f"[TelegramNotifier] send error: {exc}")

    def shutdown(self) -> None:
        """Stop accepting new messages and wait for pending tasks to finish."""
        self._shutdown = True
        self._executor.shutdown(wait=True)
