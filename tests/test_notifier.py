"""Tests for src/notifier.py."""

from unittest.mock import patch


from src.notifier import NoOpNotifier, Notifier, TelegramNotifier


class TestNotifier:

    def test_is_abstract(self):
        assert hasattr(Notifier, "__abstractmethods__")


class TestNoOpNotifier:

    def test_send_no_op(self):
        notifier = NoOpNotifier()
        notifier.send("test message")


class TestTelegramNotifier:

    def test_init(self):
        notifier = TelegramNotifier("bot_token_123", "chat_id_456")
        assert "bot_token_123" in notifier._url
        assert notifier._chat_id == "chat_id_456"

    @patch("src.notifier.requests.post")
    def test_send_posts_request(self, mock_post):
        notifier = TelegramNotifier("bot_token", "chat_id")
        notifier.send("Hello World")
        # The send method starts a thread, so we need to wait for it
        import time
        time.sleep(0.1)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["chat_id"] == "chat_id"
        assert call_kwargs["json"]["text"] == "Hello World"
        assert call_kwargs["json"]["parse_mode"] == "HTML"
        assert call_kwargs["timeout"] == 10

    @patch("src.notifier.requests.post", side_effect=Exception("Network error"))
    def test_send_suppresses_exception(self, mock_post):
        notifier = TelegramNotifier("bot_token", "chat_id")
        # Should not raise despite post failing
        notifier.send("Hello World")
        import time
        time.sleep(0.1)
        mock_post.assert_called_once()
