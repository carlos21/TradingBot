"""Tests for src/infrastructure/data_sources/live/websocket_live_datasource.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.infrastructure.data_sources.live.websocket_live_datasource import (
    WebsocketLiveDataSource,
)


class TestWebsocketLiveDataSource:

    def test_load_historical_ticks_returns_empty(self):
        ds = WebsocketLiveDataSource("MNQ", "ws://localhost:8000")
        assert ds.load_historical_ticks() == []

    def test_pair_and_url_stored(self):
        ds = WebsocketLiveDataSource("MNQ", "ws://localhost:8000")
        assert ds.pair == "MNQ"
        assert ds.ws_url == "ws://localhost:8000"

    def test_subscribe_receives_and_parses_ticks(self):
        ds = WebsocketLiveDataSource("MNQ", "ws://localhost:8000")
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            '{"time": 1000, "price": 5000.0, "volume": 10}',
            '{"time": 1001, "price": 5001.0}',
            "",  # empty message breaks loop
        ]

        with patch("src.infrastructure.data_sources.live.websocket_live_datasource.websocket.create_connection",
                   return_value=fake_ws):
            received = []
            ds.subscribe(received.append)
            import time
            time.sleep(0.05)

        assert len(received) == 2
        assert received[0] == {"time": 1000, "price": 5000.0, "volume": 10, "pair": "MNQ"}
        assert received[1] == {"time": 1001, "price": 5001.0, "volume": 0, "pair": "MNQ"}
        fake_ws.send.assert_called_once_with('{"op": "subscribe", "pair": "MNQ"}')
        fake_ws.close.assert_called_once()

    def test_subscribe_sends_subscribe_message(self):
        ds = WebsocketLiveDataSource("EURUSD", "ws://localhost:8000")
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [""]

        with patch("src.infrastructure.data_sources.live.websocket_live_datasource.websocket.create_connection",
                   return_value=fake_ws):
            ds.subscribe(lambda x: None)
            import time
            time.sleep(0.05)

        fake_ws.send.assert_called_once_with('{"op": "subscribe", "pair": "EURUSD"}')

    def test_subscribe_handles_connection_error_gracefully(self):
        ds = WebsocketLiveDataSource("MNQ", "ws://localhost:8000")

        with patch("src.infrastructure.data_sources.live.websocket_live_datasource.websocket.create_connection",
                   side_effect=ConnectionRefusedError("no server")):
            received = []
            ds.subscribe(received.append)
            import time
            time.sleep(0.05)

        assert received == []

    def test_subscribe_closes_on_exception(self):
        ds = WebsocketLiveDataSource("MNQ", "ws://localhost:8000")
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = ValueError("bad frame")

        with patch("src.infrastructure.data_sources.live.websocket_live_datasource.websocket.create_connection",
                   return_value=fake_ws):
            ds.subscribe(lambda x: None)
            import time
            time.sleep(0.05)

        fake_ws.close.assert_called_once()

    def test_subscribe_closes_on_unexpected_close(self):
        ds = WebsocketLiveDataSource("MNQ", "ws://localhost:8000")
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = Exception("connection reset")

        with patch("src.infrastructure.data_sources.live.websocket_live_datasource.websocket.create_connection",
                   return_value=fake_ws):
            ds.subscribe(lambda x: None)
            import time
            time.sleep(0.05)

        fake_ws.close.assert_called_once()

    def test_subscribe_callback_receives_parsed_tick(self):
        ds = WebsocketLiveDataSource("MNQ", "ws://localhost:8000")
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            '{"time": "123", "price": "99.5", "volume": "7"}',
            "",
        ]

        with patch("src.infrastructure.data_sources.live.websocket_live_datasource.websocket.create_connection",
                   return_value=fake_ws):
            received = []
            ds.subscribe(received.append)
            import time
            time.sleep(0.05)

        assert len(received) == 1
        assert received[0]["time"] == 123
        assert received[0]["price"] == 99.5
        assert received[0]["volume"] == 7
        assert received[0]["pair"] == "MNQ"
