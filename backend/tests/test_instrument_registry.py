"""Tests for src/services/instrument_registry.py."""
import json

import pytest

from src.domain.models import Instrument
from src.infrastructure.database.database import setup_database
from src.infrastructure.repositories.settings_repository import SettingsRepository
from src.services.instrument_registry import InstrumentRegistry
from src.strategies.liquidity_v2.instrument_params import HardcodedInstrumentCatalog


@pytest.fixture
def registry(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'instrument_registry.db'}"
    setup_database(db_url=db_path)
    from src.infrastructure.database.database import get_db_session
    get_db_session().__enter__()
    return InstrumentRegistry(SettingsRepository(), HardcodedInstrumentCatalog())


class TestInstrumentRegistry:
    def test_catalog_when_empty(self, registry):
        instruments = registry.get_all()
        assert [i.symbol for i in instruments] == ["MNQ", "MES"]
        assert instruments[0].full_name == "MNQ 09-26"
        assert instruments[0].point_value == 2.0
        assert instruments[1].full_name == "MES 09-26"
        assert instruments[1].point_value == 5.0

    def test_save_and_load_full_name_overrides(self, registry):
        registry.save([
            Instrument(symbol="MNQ", full_name="MNQ 12-26", point_value=2.0),
            Instrument(symbol="MES", full_name="MES 12-26", point_value=5.0),
        ])

        loaded = registry.get_all()
        assert [i.full_name for i in loaded] == ["MNQ 12-26", "MES 12-26"]

    def test_default_full_name_is_not_persisted(self, registry):
        registry.save([Instrument(symbol="MNQ", full_name="MNQ 09-26", point_value=2.0)])
        assert registry._settings.get("instruments") == "{}"

    def test_save_ignores_unknown_symbols(self, registry):
        registry.save([
            Instrument(symbol="MNQ", full_name="MNQ 12-26", point_value=2.0),
            Instrument(symbol="ES", full_name="ES 06-26", point_value=12.5),
        ])

        loaded = registry.get_all()
        assert [i.symbol for i in loaded] == ["MNQ", "MES"]
        assert loaded[0].full_name == "MNQ 12-26"

    def test_save_ignores_point_value_mutations(self, registry):
        registry.save([Instrument(symbol="MNQ", full_name="MNQ 12-26", point_value=99.0)])

        loaded = registry.get_all()
        assert loaded[0].point_value == 2.0

    def test_get_instruments_returns_dicts(self, registry):
        data = registry.get_instruments()
        assert data == [
            {"symbol": "MNQ", "full_name": "MNQ 09-26", "point_value": 2.0},
            {"symbol": "MES", "full_name": "MES 09-26", "point_value": 5.0},
        ]

    def test_legacy_list_format_migrates(self, registry):
        legacy = json.dumps([
            {"symbol": "MNQ", "full_name": "MNQ 03-26", "point_value": 2.0},
            {"symbol": "ES", "full_name": "ES 06-26", "point_value": 12.5},
        ])
        registry._settings.set("instruments", legacy)

        loaded = registry.get_all()
        assert [i.symbol for i in loaded] == ["MNQ", "MES"]
        # Known symbol keeps its stored full_name; unknown symbol is dropped.
        assert loaded[0].full_name == "MNQ 03-26"
        assert loaded[1].full_name == "MES 09-26"

    def test_backward_compat_from_legacy_settings(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'legacy_registry.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()
        settings_repo = SettingsRepository()
        settings_repo.set("pair", "MNQ")
        settings_repo.set("instrument", "MNQ 06-26")
        registry = InstrumentRegistry(settings_repo, HardcodedInstrumentCatalog())

        instruments = registry.get_all()
        assert instruments[0].symbol == "MNQ"
        assert instruments[0].full_name == "MNQ 06-26"

    def test_legacy_instrument_for_unknown_pair_lands_on_default(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'legacy_unknown.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()
        settings_repo = SettingsRepository()
        settings_repo.set("pair", "ES")
        settings_repo.set("instrument", "ES 06-26")
        registry = InstrumentRegistry(settings_repo, HardcodedInstrumentCatalog())

        instruments = registry.get_all()
        assert instruments[0].symbol == "MNQ"
        assert instruments[0].full_name == "ES 06-26"

    def test_malformed_json_falls_back_to_catalog(self, registry):
        registry._settings.set("instruments", "not-json")
        instruments = registry.get_all()
        assert [i.symbol for i in instruments] == ["MNQ", "MES"]
