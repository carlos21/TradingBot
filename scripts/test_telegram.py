"""Quick smoke test for Telegram notifications. Reads tokens from .env."""

import os
import sys

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

if not token or not chat_id:
    print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
    sys.exit(1)

url = f"https://api.telegram.org/bot{token}/sendMessage"
print(f"Token: {token[:8]}...{token[-4:]}")
print(f"Chat ID: {chat_id}")
print(f"URL: {url}")

resp = requests.post(url, json={
    "chat_id": chat_id,
    "text": "Test notification from TradingBot",
}, timeout=10)

print(f"Status: {resp.status_code}")
print(f"Response: {resp.text}")
