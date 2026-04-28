import contextlib
import threading
from abc import ABC, abstractmethod

import requests


class Notifier(ABC):
    @abstractmethod
    def send(self, message: str) -> None: ...


class NoOpNotifier(Notifier):
    def send(self, message: str) -> None:
        pass


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def send(self, message: str) -> None:
        t = threading.Thread(target=self._send, args=(message,), daemon=True)
        t.start()

    def _send(self, message: str) -> None:
        with contextlib.suppress(Exception):
            requests.post(self._url, json={
                "chat_id": self._chat_id,
                "text": message,
                "parse_mode": "HTML",
            }, timeout=10)
