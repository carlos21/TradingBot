"""Tests for src/infrastructure/data_sources/metatrader_datasource.py."""

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# MetaTrader5 is an optional dependency; mock it before importing the module.
_mock_mt5_module = MagicMock()
_mock_mt5_module.initialize.return_value = True
_mock_mt5_module.symbol_select.return_value = True
_mock_mt5_module.last_error.return_value = (0, "ok")
_mock_mt5_module.TIMEFRAME_M1 = 1
_mock_mt5_module.TIMEFRAME_M5 = 5
_mock_mt5_module.TIMEFRAME_M15 = 15
_mock_mt5_module.TIMEFRAME_H1 = 60
_mock_mt5_module.TIMEFRAME_H4 = 240
sys.modules["MetaTrader5"] = _mock_mt5_module

from src.infrastructure.data_sources.metatrader_datasource import (
    MetaTraderConfig,
    MetaTraderDataSource,
)


@pytest.fixture
def cfg():
    return MetaTraderConfig(
        login=123,
        password="secret",
        server="demo",
        history_hours=24,
    )


@pytest.fixture
def mock_mt5():
    """Patch the mt5 module used by the datasource."""
    with patch("src.infrastructure.data_sources.metatrader_datasource.mt5") as m:
        m.initialize.return_value = True
        m.symbol_select.return_value = True
        m.last_error.return_value = (0, "ok")
        m.TIMEFRAME_M1 = 1
        m.TIMEFRAME_M5 = 5
        m.TIMEFRAME_M15 = 15
        m.TIMEFRAME_H1 = 60
        m.TIMEFRAME_H4 = 240
        yield m


class TestMetaTraderDataSourceInit:

    def test_init_stores_config(self, mock_mt5, cfg):
        ds = MetaTraderDataSource("EURUSD", cfg)
        assert ds.symbol == "EURUSD"
        assert ds.pair == "EURUSD"
        assert ds.creds == {"login": 123, "password": "secret", "server": "demo"}
        assert ds._history_hours == 24

    def test_init_initializes_mt5(self, mock_mt5, cfg):
        MetaTraderDataSource("EURUSD", cfg)
        mock_mt5.initialize.assert_called_once_with(
            login=123, password="secret", server="demo"
        )

    def test_init_fails_when_mt5_initialize_returns_false(self, mock_mt5, cfg):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (1, "init failed")
        with pytest.raises(RuntimeError, match="MT5 init failed"):
            MetaTraderDataSource("EURUSD", cfg)


class TestMetaTraderDataSourceLoadHistoricalBars:

    def test_invalid_timeframe_raises(self, mock_mt5, cfg):
        ds = MetaTraderDataSource("EURUSD", cfg)
        with pytest.raises(ValueError, match="Unsupported timeframe: 10m"):
            ds.load_historical_bars("10m")

    def test_symbol_select_failure_returns_empty(self, mock_mt5, cfg):
        mock_mt5.symbol_select.return_value = False
        ds = MetaTraderDataSource("EURUSD", cfg)
        assert ds.load_historical_bars("1m") == []

    def test_empty_rates_returns_empty(self, mock_mt5, cfg):
        mock_mt5.copy_rates_range.return_value = None
        ds = MetaTraderDataSource("EURUSD", cfg)
        assert ds.load_historical_bars("1m") == []

    def test_load_historical_bars_converts_timezones(self, mock_mt5, cfg):
        base_ts = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        mock_mt5.copy_rates_range.return_value = [
            {"time": base_ts, "open": 1.1000, "high": 1.1100, "low": 1.0900,
             "close": 1.1050, "tick_volume": 100},
        ]
        ds = MetaTraderDataSource("EURUSD", cfg)
        bars = ds.load_historical_bars("1m")
        assert len(bars) == 1
        assert bars[0]["open"] == 1.1000
        assert bars[0]["high"] == 1.1100
        assert bars[0]["low"] == 1.0900
        assert bars[0]["close"] == 1.1050
        assert bars[0]["volume"] == 100
        assert bars[0]["pair"] == "EURUSD"
        assert isinstance(bars[0]["time"], int)

    def test_load_historical_bars_empty_list(self, mock_mt5, cfg):
        mock_mt5.copy_rates_range.return_value = []
        ds = MetaTraderDataSource("EURUSD", cfg)
        assert ds.load_historical_bars("1m") == []

    def test_supported_timeframes_map(self, mock_mt5, cfg):
        ds = MetaTraderDataSource("EURUSD", cfg)
        base_ts = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        mock_mt5.copy_rates_range.return_value = [
            {"time": base_ts, "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0, "tick_volume": 1},
        ]
        for tf in ("1m", "5m", "15m", "1h", "4h"):
            bars = ds.load_historical_bars(tf)
            assert len(bars) == 1, tf


class TestMetaTraderDataSourceSubscribe:

    def test_subscribe_replays_history_and_stream_ticks(self, mock_mt5, cfg):
        base_ts = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        mock_mt5.copy_rates_range.return_value = [
            {"time": base_ts, "open": 1.1000, "high": 1.1100, "low": 1.0900,
             "close": 1.1050, "tick_volume": 100},
            {"time": base_ts + 60, "open": 1.1050, "high": 1.1150, "low": 1.1000,
             "close": 1.1100, "tick_volume": 200},
        ]
        ds = MetaTraderDataSource("EURUSD", cfg)

        mock_conn = MagicMock()
        mock_conn.recv.side_effect = [
            b"EURUSD 1.1200\n",
            b"EURUSD 1.1210\n",
            b"",  # close connection
        ]
        mock_srv = MagicMock()
        mock_srv.accept.return_value = (mock_conn, ("127.0.0.1", 12345))

        with patch("src.infrastructure.data_sources.metatrader_datasource.socket.socket",
                   return_value=mock_srv):
            received = []
            ds.subscribe(received.append, from_time=0)

        historical = [m for m in received if "open" in m]
        ticks = [m for m in received if "price" in m]
        assert len(historical) == 2
        assert len(ticks) == 2
        assert ticks[0]["price"] == 1.1200
        assert ticks[0]["pair"] == "EURUSD"
        assert ticks[1]["price"] == 1.1210
        mock_conn.close.assert_called_once()
        mock_srv.close.assert_called_once()

    def test_subscribe_honors_from_time(self, mock_mt5, cfg):
        base_ts = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        mock_mt5.copy_rates_range.return_value = [
            {"time": base_ts, "open": 1.1000, "high": 1.1100, "low": 1.0900,
             "close": 1.1050, "tick_volume": 100},
            {"time": base_ts + 60, "open": 1.1050, "high": 1.1150, "low": 1.1000,
             "close": 1.1100, "tick_volume": 200},
        ]
        ds = MetaTraderDataSource("EURUSD", cfg)
        # Use converted bar times to account for timezone offset.
        history = ds.load_historical_bars("1m")
        assert len(history) == 2

        mock_conn = MagicMock()
        mock_conn.recv.side_effect = [b""]
        mock_srv = MagicMock()
        mock_srv.accept.return_value = (mock_conn, ("127.0.0.1", 12345))

        with patch("src.infrastructure.data_sources.metatrader_datasource.socket.socket",
                   return_value=mock_srv):
            received = []
            ds.subscribe(received.append, from_time=history[0]["time"])

        historical = [m for m in received if "open" in m]
        assert len(historical) == 1
        assert historical[0]["time"] == history[1]["time"]

    def test_subscribe_history_error_is_logged(self, mock_mt5, cfg):
        mock_mt5.copy_rates_range.side_effect = RuntimeError("history failed")
        ds = MetaTraderDataSource("EURUSD", cfg)

        mock_conn = MagicMock()
        mock_conn.recv.side_effect = [b""]
        mock_srv = MagicMock()
        mock_srv.accept.return_value = (mock_conn, ("127.0.0.1", 12345))

        with patch("src.infrastructure.data_sources.metatrader_datasource.socket.socket",
                   return_value=mock_srv):
            received = []
            ds.subscribe(received.append, from_time=0)

        assert received == []

    def test_subscribe_malformed_tick_is_skipped(self, mock_mt5, cfg):
        base_ts = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        mock_mt5.copy_rates_range.return_value = [
            {"time": base_ts, "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0, "tick_volume": 1},
        ]
        ds = MetaTraderDataSource("EURUSD", cfg)

        mock_conn = MagicMock()
        mock_conn.recv.side_effect = [
            b"EURUSD\n",        # missing price
            b"EURUSD abc\n",    # invalid price
            b"\n",              # empty line after strip
            b"EURUSD 2.0\n",
            b"",
        ]
        mock_srv = MagicMock()
        mock_srv.accept.return_value = (mock_conn, ("127.0.0.1", 12345))

        with patch("src.infrastructure.data_sources.metatrader_datasource.socket.socket",
                   return_value=mock_srv):
            received = []
            ds.subscribe(received.append, from_time=0)

        ticks = [m for m in received if "price" in m]
        assert len(ticks) == 1
        assert ticks[0]["price"] == 2.0

    def test_subscribe_server_exception_before_accept_closes_only_server(self, mock_mt5, cfg):
        base_ts = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        mock_mt5.copy_rates_range.return_value = [
            {"time": base_ts, "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0, "tick_volume": 1},
        ]
        ds = MetaTraderDataSource("EURUSD", cfg)

        mock_srv = MagicMock()
        mock_srv.accept.side_effect = RuntimeError("accept failed")

        with patch("src.infrastructure.data_sources.metatrader_datasource.socket.socket",
                   return_value=mock_srv):
            received = []
            with pytest.raises(RuntimeError, match="accept failed"):
                ds.subscribe(received.append, from_time=0)

        assert len(received) == 1  # history replayed before accept failure
        mock_srv.close.assert_called_once()


class TestMetaTraderDataSourcePause:

    def test_pause_is_no_op(self, mock_mt5, cfg):
        ds = MetaTraderDataSource("EURUSD", cfg)
        ds.pause()  # should not raise
