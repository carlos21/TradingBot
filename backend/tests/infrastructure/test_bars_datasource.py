"""Tests for src/infrastructure/data_sources/bars_datasource.py."""

from abc import ABC

import pytest

from src.infrastructure.data_sources.bars_datasource import BarsDataSource


class TestBarsDataSource:

    def test_is_abstract(self):
        assert issubclass(BarsDataSource, ABC)

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BarsDataSource()

    def test_subclass_must_implement_load_1m_bars(self):
        class BadDataSource(BarsDataSource):
            pass

        with pytest.raises(TypeError):
            BadDataSource()

    def test_valid_subclass(self):
        class GoodDataSource(BarsDataSource):
            pair = "MNQ"

            def load_1m_bars(self):
                return [{"time": 1, "open": 1.0, "high": 2.0, "low": 0.5,
                         "close": 1.5, "volume": 10, "pair": "MNQ"}]

        ds = GoodDataSource()
        bars = ds.load_1m_bars()
        assert len(bars) == 1
        assert bars[0]["pair"] == "MNQ"
