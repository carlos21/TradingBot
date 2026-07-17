"""Tests for src/services/instrument_registry.py."""
import pytest

from src.infrastructure.database.database import setup_database
from src.infrastructure.repositories.settings_repository import SettingsRepository
from src.services.instrument_registry import InstrumentRegistry


@pytest.fixture
def registry(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'instrument_registry.db'}"
    setup_database(db_url=db_path)
    from src.infrastructure.database.database import get_db_session
    get_db_session().__enter__()
    return InstrumentRegistry(SettingsRepository())


class TestInstrumentRegistry:
    def test_default_when_empty(self, registry):
        instruments = registry.get_all()
        assert len(instruments) == 1
        assert instruments[0].symbol == "MNQ"
        assert instruments[0].full_name == "MNQ 09-26"
        assert instruments[0].point_value == 2.0

    def test_save_and_load(self, registry):
        instruments = [
            {
                "symbol": "ES",
                "full_name": "ES 06-26",
                "point_value": 12.5,
            }
        ]
        registry.save([registry._deserialize(item) for item in instruments])

        loaded = registry.get_all()
        assert len(loaded) == 1
        assert loaded[0].symbol == "ES"
        assert loaded[0].full_name == "ES 06-26"
        assert loaded[0].point_value == 12.5

    def test_get_instruments_returns_dicts(self, registry):
        registry.save([InstrumentRegistry._deserialize({
            "symbol": "NQ",
            "full_name": "NQ 09-26",
        })])
        data = registry.get_instruments()
        assert data == [{
            "symbol": "NQ",
            "full_name": "NQ 09-26",
            "point_value": 2.0,
        }]

    def test_backward_compat_from_legacy_settings(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'legacy_registry.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()
        settings_repo = SettingsRepository()
        settings_repo.set("pair", "ES")
        settings_repo.set("instrument", "ES 06-26")
        registry = InstrumentRegistry(settings_repo)

        instruments = registry.get_all()
        assert len(instruments) == 1
        assert instruments[0].symbol == "ES"
        assert instruments[0].full_name == "ES 06-26"

    def test_ignores_unknown_fields(self, registry):
        instrument = registry._deserialize({
            "symbol": "YM",
            "full_name": "YM 09-26",
            "extra_field": "ignored",
        })
        assert instrument.symbol == "YM"
        assert instrument.full_name == "YM 09-26"
        assert not hasattr(instrument, "extra_field")

    def test_malformed_json_falls_back_to_default(self, registry):
        registry._settings.set("instruments", "not-json")
        instruments = registry.get_all()
        assert len(instruments) == 1
        assert instruments[0].symbol == "MNQ"
