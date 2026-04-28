from unittest.mock import patch

from src.notifier import NoOpNotifier, TelegramNotifier
from tests.fakes import FakeNotifier


def test_noop_notifier_does_nothing():
    n = NoOpNotifier()
    n.send("test")  # should not raise


def test_fake_notifier_records_messages():
    n = FakeNotifier()
    n.send("error 1")
    n.send("error 2")
    assert n.messages == ["error 1", "error 2"]


@patch("src.notifier.requests.post")
def test_telegram_notifier_posts_to_api(mock_post):
    n = TelegramNotifier(bot_token="TOKEN123", chat_id="CHAT456")
    n._send("hello")

    mock_post.assert_called_once_with(
        "https://api.telegram.org/botTOKEN123/sendMessage",
        json={"chat_id": "CHAT456", "text": "hello", "parse_mode": "HTML"},
        timeout=10,
    )


@patch("src.notifier.requests.post", side_effect=Exception("network down"))
def test_telegram_notifier_swallows_errors(_mock_post):
    n = TelegramNotifier(bot_token="TOKEN", chat_id="CHAT")
    n._send("hello")  # should not raise
