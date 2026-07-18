"""Service for loading and persisting the instrument registry.

The set of supported instruments comes from an injected ``InstrumentCatalog``
(hardcoded in ``src.strategies.liquidity_v2.instrument_params``) — adding an
instrument requires tuned point parameters, so it is a code change, not a
settings operation.  Only each instrument's ``full_name`` (which changes at
every contract rollover) is user-editable; this registry persists those
full-name overrides in ``AppSetting`` under the ``instruments`` key.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict

from src.domain.models import Instrument
from src.domain.repositories import (
    IInstrumentRegistry,
    InstrumentCatalog,
    SettingsRepository as ISettingsRepository,
)

logger = logging.getLogger(__name__)


class InstrumentRegistry(IInstrumentRegistry):
    """Catalog-driven instrument registry with DB-backed full-name overrides."""

    _KEY = "instruments"

    def __init__(self, settings_repo: ISettingsRepository, catalog: InstrumentCatalog):
        self._settings = settings_repo
        self._catalog = catalog

    def get_all(self) -> list[Instrument]:
        """Return every catalog instrument with its full-name overrides applied."""
        overrides = self._load_overrides()
        return [
            Instrument(
                symbol=inst.symbol,
                full_name=overrides.get(inst.symbol, inst.full_name),
                point_value=inst.point_value,
            )
            for inst in self._catalog.get_defaults()
        ]

    def save(self, instruments: list[Instrument]) -> None:
        """Persist full-name overrides for known instruments.

        Unknown symbols and attempts to change anything other than
        ``full_name`` are ignored (the catalog is the source of truth).
        """
        defaults = {inst.symbol: inst.full_name for inst in self._catalog.get_defaults()}
        overrides: dict[str, str] = {}
        for inst in instruments:
            if inst.symbol not in defaults:
                logger.warning("Ignoring unknown instrument symbol %r on save", inst.symbol)
                continue
            if inst.full_name and inst.full_name != defaults[inst.symbol]:
                overrides[inst.symbol] = inst.full_name
        self._settings.set(self._KEY, json.dumps(overrides))

    def get_instruments(self) -> list[dict]:
        """Return instruments as plain dictionaries for API responses."""
        return [asdict(inst) for inst in self.get_all()]

    def _load_overrides(self) -> dict[str, str]:
        """Read persisted full-name overrides, migrating legacy formats."""
        raw = self._settings.get(self._KEY)
        if raw:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                data = None
            if isinstance(data, dict):
                # Current format: {symbol: full_name}
                return self._known_overrides(data)
            if isinstance(data, list):
                # Legacy format: [{symbol, full_name, point_value}, ...]
                dropped = [item.get("symbol") for item in data
                           if isinstance(item, dict) and not self._is_known(item.get("symbol"))]
                if dropped:
                    logger.warning(
                        "Dropping unsupported instruments from stored registry: %s", dropped)
                return self._known_overrides(
                    {item.get("symbol"): item.get("full_name")
                     for item in data if isinstance(item, dict)}
                )

        # Backward compatibility: legacy pair/instrument settings keys.
        instrument = self._settings.get("instrument")
        if instrument:
            pair = self._settings.get("pair")
            symbol = pair if self._is_known(pair) else self._catalog.default_symbol()
            return {symbol: instrument}
        return {}

    def _known_overrides(self, mapping: dict) -> dict[str, str]:
        """Keep only overrides for catalog symbols with a non-empty full_name."""
        return {
            symbol: full_name
            for symbol, full_name in mapping.items()
            if self._is_known(symbol) and full_name
        }

    def _is_known(self, symbol: object) -> bool:
        return any(inst.symbol == symbol for inst in self._catalog.get_defaults())
