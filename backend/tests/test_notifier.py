"""Tests for src/notifier.py."""

import logging
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
        notifier.shutdown()
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
        notifier.shutdown()
        mock_post.assert_called_once()

    @patch("src.notifier.requests.post")
    def test_send_html_escapes_message(self, mock_post):
        notifier = TelegramNotifier("bot_token", "chat_id")
        notifier.send("Profit > 0 & loss < 5")
        notifier.shutdown()
        text = mock_post.call_args.kwargs["json"]["text"]
        assert "&gt;" in text
        assert "&lt;" in text
        assert "&amp;" in text

    @patch("src.notifier.requests.post")
    def test_send_truncates_long_message(self, mock_post):
        notifier = TelegramNotifier("bot_token", "chat_id")
        notifier.send("x" * 5000)
        notifier.shutdown()
        text = mock_post.call_args.kwargs["json"]["text"]
        assert len(text) <= TelegramNotifier.MAX_MESSAGE_LENGTH
        assert text.endswith("...")

    @patch("src.notifier.requests.post")
    def test_send_logs_http_failure(self, mock_post, caplog):
        response = mock_post.return_value
        response.ok = False
        response.status_code = 400
        response.text = "Bad Request: chat not found"
        notifier = TelegramNotifier("bot_token", "chat_id")
        with caplog.at_level(logging.ERROR):
            notifier.send("Hello")
            notifier.shutdown()
        assert "send failed" in caplog.text

    @patch("src.notifier.requests.post", side_effect=Exception("Network error"))
    def test_send_logs_network_error(self, mock_post, caplog):
        notifier = TelegramNotifier("bot_token", "chat_id")
        with caplog.at_level(logging.ERROR):
            notifier.send("Hello")
            notifier.shutdown()
        assert "send error" in caplog.text

    @patch("src.notifier.requests.post")
    def test_send_after_shutdown_is_ignored(self, mock_post, caplog):
        notifier = TelegramNotifier("bot_token", "chat_id")
        notifier.shutdown()
        with caplog.at_level(logging.WARNING):
            notifier.send("Hello")
        assert "after shutdown" in caplog.text
        mock_post.assert_not_called()
