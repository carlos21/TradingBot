"""Tests for src/data_sources/combined_datasource.py."""

from abc import ABC

import pytest

from src.data_sources.combined_datasource import CombinedDataSource


class TestCombinedDataSource:

    def test_is_abstract(self):
        assert issubclass(CombinedDataSource, ABC)

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            CombinedDataSource()

    def test_subclass_must_implement_methods(self):
        class BadDataSource(CombinedDataSource):
            pass

        with pytest.raises(TypeError):
            BadDataSource()

    def test_valid_subclass(self):
        class GoodDataSource(CombinedDataSource):
            def load_historical_bars(self, timeframe="1m"):
                return []
            def subscribe(self, callback, from_time=0):
                pass
            def pause(self):
                pass

        ds = GoodDataSource()
        assert ds.load_historical_bars() == []
        ds.pause()
