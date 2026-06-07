import contextlib
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
    def __init__(self, bot_token: str, chat_id: str, max_workers: int = 4):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="telegram")

    def send(self, message: str) -> None:
        self._executor.submit(self._send, message)

    def _send(self, message: str) -> None:
        with contextlib.suppress(Exception):
            requests.post(self._url, json={
                "chat_id": self._chat_id,
                "text": message,
                "parse_mode": "HTML",
            }, timeout=10)
