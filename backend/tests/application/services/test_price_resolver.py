"""Tests for src.application.services.price_resolver."""
from unittest.mock import MagicMock

import pytest

from src.application.services.price_resolver import PriceResolver


@pytest.fixture
def bars_loader():
    loader = MagicMock()
    loader.pair = "MNQ"
    loader._1m_buffer = []
    loader._last_bar_close = 0
    loader.data_source = MagicMock()
    loader.data_source.load_historical_bars.return_value = []
    return loader


class TestPriceResolver:
    def test_returns_last_1m_buffer_close(self, bars_loader):
        bars_loader._1m_buffer = [
            {"time": 1, "close": 100.0},
            {"time": 2, "close": 101.0},
        ]
        resolver = PriceResolver(bars_loader)
        assert resolver.current_price() == 101.0

    def test_falls_back_to_historical_bars(self, bars_loader):
        bars_loader.data_source.load_historical_bars.return_value = [
            {"time": 1, "close": 200.0},
            {"time": 2, "close": 205.0},
        ]
        resolver = PriceResolver(bars_loader)
        assert resolver.current_price() == 205.0
        bars_loader.data_source.load_historical_bars.assert_called_once_with("1m", pair="MNQ")

    def test_falls_back_to_last_bar_close(self, bars_loader):
        bars_loader._last_bar_close = 300.0
        resolver = PriceResolver(bars_loader)
        assert resolver.current_price() == 300.0

    def test_returns_none_when_no_data_available(self, bars_loader):
        resolver = PriceResolver(bars_loader)
        assert resolver.current_price() is None
