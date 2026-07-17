"""Repository for NinjaTrader account configurations."""
from __future__ import annotations

from src.config.models import AccountConfig
from src.domain.repositories import AccountRepository
from src.infrastructure.database.database import NtAccount
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.repositories.base import SQLRepositoryBase, retry_on_sqlite_lock


class NtAccountRepository(SQLRepositoryBase, AccountRepository):
    """CRUD for NtAccount rows."""

    def __init__(self, db: DatabaseProtocol | None = None):
        super().__init__(db)

    @staticmethod
    def _normalize_instrument_symbols(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v).strip().upper() for v in value if str(v).strip()]
        if isinstance(value, str):
            return [value.strip().upper()] if value.strip() else []
        return []

    def get_account(self, name: str) -> AccountConfig | None:
        with self._session() as session:
            row = session.query(NtAccount).filter_by(name=name).first()
            if row:
                return AccountConfig(
                    name=row.name, risk_usd=row.risk_usd,
                    risk_pct=row.risk_pct, rr_ratio=row.rr_ratio,
                    live_enabled=bool(row.live_enabled) if row.live_enabled is not None else True,
                    instrument_symbols=self._normalize_instrument_symbols(row.instrument_symbols),
                )
            return None

    def list_accounts(self) -> list[AccountConfig]:
        with self._session() as session:
            rows = session.query(NtAccount).all()
            return [
                AccountConfig(
                    name=r.name, risk_usd=r.risk_usd, risk_pct=r.risk_pct,
                    rr_ratio=r.rr_ratio,
                    live_enabled=bool(r.live_enabled) if r.live_enabled is not None else True,
                    instrument_symbols=self._normalize_instrument_symbols(r.instrument_symbols),
                )
                for r in rows
            ]

    @retry_on_sqlite_lock()
    def upsert(self, name: str, risk_usd: float | None = None,
               risk_pct: float | None = None, rr_ratio: float | None = None,
               live_enabled: bool = True,
               instrument_symbols: list[str] | None = None) -> None:
        instrument_symbols = self._normalize_instrument_symbols(instrument_symbols)
        with self._session() as session:
            try:
                row = session.query(NtAccount).filter_by(name=name).first()
                if row:
                    row.risk_usd = risk_usd
                    row.risk_pct = risk_pct
                    row.rr_ratio = rr_ratio
                    row.live_enabled = 1 if live_enabled else 0
                    row.instrument_symbols = instrument_symbols
                else:
                    row = NtAccount(
                        name=name, risk_usd=risk_usd, risk_pct=risk_pct,
                        rr_ratio=rr_ratio, live_enabled=1 if live_enabled else 0,
                        instrument_symbols=instrument_symbols,
                    )
                    session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                raise

    @retry_on_sqlite_lock()
    def delete(self, name: str) -> None:
        with self._session() as session:
            try:
                row = session.query(NtAccount).filter_by(name=name).first()
                if row:
                    session.delete(row)
                    session.commit()
            except Exception:
                session.rollback()
                raise

    @retry_on_sqlite_lock()
    def clear_all(self) -> None:
        with self._session() as session:
            try:
                session.query(NtAccount).delete()
                session.commit()
            except Exception:
                session.rollback()
                raise
