"""Repository for NinjaTrader account configurations."""
from __future__ import annotations

from src.config.models import AccountConfig
from src.domain.repositories import AccountRepository
from src.infrastructure.database.database import NtAccount
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.repositories.base import SQLRepositoryBase


class NtAccountRepository(SQLRepositoryBase, AccountRepository):
    """CRUD for NtAccount rows."""

    def __init__(self, db: DatabaseProtocol | None = None):
        super().__init__(db)

    def get_account(self, name: str) -> AccountConfig | None:
        with self._session() as session:
            row = session.query(NtAccount).filter_by(name=name).first()
            if row:
                return AccountConfig(
                    name=row.name, risk_usd=row.risk_usd,
                    risk_pct=row.risk_pct, rr_ratio=row.rr_ratio
                )
            return None

    def list_accounts(self) -> list[AccountConfig]:
        with self._session() as session:
            rows = session.query(NtAccount).all()
            return [
                AccountConfig(name=r.name, risk_usd=r.risk_usd, risk_pct=r.risk_pct, rr_ratio=r.rr_ratio)
                for r in rows
            ]

    def upsert(self, name: str, risk_usd: float | None = None,
               risk_pct: float | None = None, rr_ratio: float | None = None) -> None:
        with self._session() as session:
            try:
                row = session.query(NtAccount).filter_by(name=name).first()
                if row:
                    row.risk_usd = risk_usd
                    row.risk_pct = risk_pct
                    row.rr_ratio = rr_ratio
                else:
                    row = NtAccount(name=name, risk_usd=risk_usd, risk_pct=risk_pct, rr_ratio=rr_ratio)
                    session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                raise

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

    def clear_all(self) -> None:
        with self._session() as session:
            try:
                session.query(NtAccount).delete()
                session.commit()
            except Exception:
                session.rollback()
                raise
