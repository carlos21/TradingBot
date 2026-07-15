"""Tests for src/infrastructure/data_sources/live/live_datasource.py."""

from abc import ABC
from collections.abc import Callable

import pytest

from src.infrastructure.data_sources.live.live_datasource import LiveDataSource


class TestLiveDataSource:

    def test_is_abstract(self):
        assert issubclass(LiveDataSource, ABC)

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            LiveDataSource()

    def test_subclass_must_implement_methods(self):
        class BadDataSource(LiveDataSource):
            pass

        with pytest.raises(TypeError):
            BadDataSource()

    def test_valid_subclass(self):
        class GoodDataSource(LiveDataSource):
            pair = "MNQ"

            def subscribe(self, callback: Callable[[dict], None]) -> None:
                callback({"time": 1, "price": 100.0, "volume": 10, "pair": "MNQ"})

            def load_historical_ticks(self):
                return [{"time": 1, "price": 100.0, "volume": 10, "pair": "MNQ"}]

        ds = GoodDataSource()
        received = []
        ds.subscribe(received.append)
        assert len(received) == 1
        assert received[0]["pair"] == "MNQ"
        assert ds.load_historical_ticks() == [{"time": 1, "price": 100.0,
                                               "volume": 10, "pair": "MNQ"}]
