"""Tests for src.application.services.virtual_time_resolver."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.application.services.virtual_time_resolver import VirtualTimeResolver


@pytest.fixture
def bars_loader():
    loader = MagicMock()
    loader.pair = "MNQ"
    loader._last_played_ts = 0
    loader.data_source = MagicMock()
    loader.data_source.load_historical_bars.return_value = []
    return loader


class TestVirtualTimeResolver:
    def test_returns_last_played_timestamp(self, bars_loader):
        bars_loader._last_played_ts = 1_700_000_000
        resolver = VirtualTimeResolver(bars_loader)
        assert resolver.now() == 1_700_000_000

    def test_falls_back_to_historical_bars(self, bars_loader):
        bars_loader.data_source.load_historical_bars.return_value = [
            {"time": 1_700_000_100},
            {"time": 1_700_000_200},
        ]
        resolver = VirtualTimeResolver(bars_loader)
        assert resolver.now() == 1_700_000_200
        bars_loader.data_source.load_historical_bars.assert_called_once_with("1m", pair="MNQ")

    def test_falls_back_to_system_time(self, bars_loader):
        resolver = VirtualTimeResolver(bars_loader)
        before = datetime.now(timezone.utc).timestamp()
        result = resolver.now()
        after = datetime.now(timezone.utc).timestamp()
        assert before <= result <= after
