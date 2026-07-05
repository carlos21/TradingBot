"""Quick smoke test for Telegram notifications. Reads tokens from .env."""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests  # noqa: E402


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        return 1

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    print("Token: <redacted>")
    print(f"Chat ID: {chat_id}")
    print(f"URL: {url[:url.rfind('/')]}/<redacted>/sendMessage")

    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": "Test notification from Liquid",
        }, timeout=10)
    except Exception as exc:
        print(f"Request failed: {exc}")
        return 1

    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
    return 0 if resp.ok else 1


if __name__ == "__main__":
    sys.exit(main())
