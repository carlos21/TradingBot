"""Service for loading and persisting the instrument registry."""
from __future__ import annotations

import json
from dataclasses import asdict, fields

from src.domain.models import Instrument
from src.domain.repositories import IInstrumentRegistry, SettingsRepository as ISettingsRepository


class InstrumentRegistry(IInstrumentRegistry):
    """JSON-backed registry of instruments stored in AppSetting under ``instruments``."""

    _KEY = "instruments"
    _DEFAULT_SYMBOL = "MNQ"
    _DEFAULT_FULL_NAME = "MNQ 09-26"

    def __init__(self, settings_repo: ISettingsRepository):
        self._settings = settings_repo

    @classmethod
    def default_instrument(cls) -> Instrument:
        return Instrument(
            symbol=cls._DEFAULT_SYMBOL,
            full_name=cls._DEFAULT_FULL_NAME,
        )

    def get_all(self) -> list[Instrument]:
        """Return registered instruments, deriving from legacy settings if missing."""
        raw = self._settings.get(self._KEY)
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list) and data:
                    return [self._deserialize(item) for item in data]
            except (json.JSONDecodeError, TypeError):
                pass

        # Backward compatibility: build a single instrument from legacy settings.
        pair = self._settings.get("pair")
        instrument = self._settings.get("instrument")
        if pair is None and instrument is None:
            return [self.default_instrument()]
        symbol = pair or self._DEFAULT_SYMBOL
        full_name = instrument or symbol
        return [Instrument(symbol=symbol, full_name=full_name)]

    def save(self, instruments: list[Instrument]) -> None:
        """Persist the registry as a JSON list."""
        data = [asdict(inst) for inst in instruments]
        self._settings.set(self._KEY, json.dumps(data))

    def get_instruments(self) -> list[dict]:
        """Return instruments as plain dictionaries for API responses."""
        return [asdict(inst) for inst in self.get_all()]

    @staticmethod
    def _deserialize(item: dict) -> Instrument:
        """Build an Instrument from a dictionary, ignoring unknown fields."""
        known = {f.name for f in fields(Instrument)}
        cleaned = {k: v for k, v in item.items() if k in known}
        if "point_value" in cleaned:
            cleaned["point_value"] = float(cleaned["point_value"])
        return Instrument(**cleaned)
